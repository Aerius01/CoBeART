# Copyright © 2018 Naturalpoint
#
# Licensed under the Apache License, Version 2.0 (the "License")
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


# OptiTrack NatNet direct depacketization sample for Python 3.x
#
# Uses the Python NatNetClient.py library to establish a connection (by creating a NatNetClient),
# and receive data via a NatNet connection and decode it using the NatNetClient library.

import argparse
import logging
import math
import signal
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import FrameType
from typing import Any, Protocol

import socketio

from cobeart.packagesender.metrics import MetricsTracker
from cobeart.packagesender.sender import PayloadSender, StrictJson

from cobeart.optitrackclient.NatNetClient import NatNetClient
import cobeart.optitrackclient.DataDescriptions as DataDescriptions
import cobeart.optitrackclient.MoCapData as MoCapData
from cobeart.optitrackclient.tracked_bodies import apply_rigid_body
from cobeart.optitrackclient.transform import ArenaPose, Quaternion, Vec3
from cobeart.settings.config import ConfigError, Settings, load_settings, validate_hub_url

logger: logging.Logger = logging.getLogger(__name__)

FAILURE_POLL_S: float = 0.1

FrameData = Mapping[str, Any]  # NatNet's per-frame dict; only "timestamp" (s since Motive start) is used
FrameListener = Callable[[FrameData], None]
RigidBodyListener = Callable[[int, Vec3, Quaternion, bool], None]


class MotionSource(Protocol):
    """A NatNet-style source that calls the rigid-body listener per body, then the frame listener, per frame."""

    new_frame_listener: FrameListener | None
    rigid_body_listener: RigidBodyListener | None

    def run(self) -> bool: ...

    def shutdown(self) -> None: ...


class FramePipeline:
    """Collects the bodies of one mocap frame and hands the frame to the sender with its mocap timestamp.

    Both callbacks run on the motion source's receive thread, rigid bodies first, then the frame. An exception in
    either is logged and sets `failure` instead of killing that thread; the main thread watches `failure` and exits.
    """

    def __init__(self, sender: PayloadSender, max_num_objects: int, failure: threading.Event) -> None:
        self._sender: PayloadSender = sender
        self._failure: threading.Event = failure
        self._max_num_objects: int = max_num_objects
        self._bodies: dict[int, list[float]] = {}  # [x, y, z, qx, qy, qz, qw] in arena axes (see transform.py)
        self._dropped_ids: set[int] = set()  # so dropout and return are each logged once
        self._warned_ids: set[int] = set()

    def attach(self, source: MotionSource) -> None:
        """Register this pipeline's callbacks on a motion source."""
        source.new_frame_listener = self.receive_new_frame
        source.rigid_body_listener = self.receive_rigid_body_frame

    def receive_rigid_body_frame(self, new_id: int, position: Vec3, rotation: Quaternion, tracking_valid: bool) -> None:
        """Store one body's arena pose for the current frame; untracked or invalid bodies are removed."""
        if self._failure.is_set():
            return
        try:
            self._store_rigid_body(new_id, position, rotation, tracking_valid)
        except Exception:
            logger.exception("Rigid-body callback failed for body %d; stopping the motion pipeline", new_id)
            self._failure.set()

    def receive_new_frame(self, data_dict: FrameData) -> None:
        """Pass the frame's bodies to the sender, clocked by the mocap frame timestamp."""
        if self._failure.is_set():
            return
        try:
            self._send_frame(data_dict)
        except Exception:
            logger.exception("Frame callback failed; stopping the motion pipeline")
            self._failure.set()

    def _store_rigid_body(self, new_id: int, position: Vec3, rotation: Quaternion, tracking_valid: bool) -> None:
        if new_id < self._max_num_objects:
            apply_rigid_body(self._bodies, self._dropped_ids, new_id, position, rotation, tracking_valid)
        elif new_id not in self._warned_ids:
            self._warned_ids.add(new_id)
            logger.warning("Ignoring rigid body ID %d: IDs must be below max_num_objects (%d), see config/cobeart.yaml",
                           new_id, self._max_num_objects)

    def _send_frame(self, data_dict: FrameData) -> None:
        bodies: dict[int, ArenaPose] = {
            body_id: ArenaPose(position=(v[0], v[1], v[2]), orientation=(v[3], v[4], v[5], v[6]))
            for body_id, v in self._bodies.items()
        }
        self._sender.handle_frame(float(data_dict["timestamp"]), bodies)


@dataclass(frozen=True, slots=True)
class CirclePath:
    """A body walking a horizontal circle about the arena centre, counterclockwise seen from above, facing forward."""
    body_id: int
    radius_mm: float
    period_s: float
    height_mm: float
    phase_rad: float = 0.0

    @property
    def speed_mm_s(self) -> float:
        return 2.0 * math.pi * self.radius_mm / self.period_s


DEFAULT_SYNTHETIC_PATHS: tuple[CirclePath, ...] = (
    CirclePath(body_id=0, radius_mm=1500.0, period_s=6.0, height_mm=1200.0),
    CirclePath(body_id=1, radius_mm=900.0, period_s=4.0, height_mm=1000.0, phase_rad=math.pi),
    CirclePath(body_id=2, radius_mm=2200.0, period_s=10.0, height_mm=1700.0, phase_rad=math.pi / 2),
)


def arena_pose_on_circle(path: CirclePath, t: float) -> ArenaPose:
    """Arena pose (mm, quaternion) of a circle-walking body at time t; heading is the direction of travel."""
    theta: float = path.phase_rad + 2.0 * math.pi * t / path.period_s
    # Identity faces +y; yaw theta about +z turns it to (-sin, cos), the counterclockwise tangent at angle theta.
    return ArenaPose(
        position=(path.radius_mm * math.cos(theta), path.radius_mm * math.sin(theta), path.height_mm),
        orientation=(0.0, 0.0, math.sin(theta / 2.0), math.cos(theta / 2.0)),
    )


def arena_to_optitrack(pose: ArenaPose) -> tuple[Vec3, Quaternion]:
    """Inverse of the D5 remap (x = -X, y = Z, z = Y): arena mm and quaternion to OptiTrack metres and quaternion."""
    x, y, z = pose.position
    qx, qy, qz, qw = pose.orientation
    return (-x / 1000.0, z / 1000.0, y / 1000.0), (-qx, qz, qy, qw)


class SyntheticNatNetSource:
    """Offline MotionSource: emits scripted OptiTrack-native poses through the NatNet callbacks at a fixed rate."""

    def __init__(
        self,
        paths: Sequence[CirclePath],
        rate_hz: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.new_frame_listener: FrameListener | None = None
        self.rigid_body_listener: RigidBodyListener | None = None
        self._paths: tuple[CirclePath, ...] = tuple(paths)
        self.rate_hz: float = rate_hz
        self._clock: Callable[[], float] = clock
        self._frame_number: int = 0
        self._stop: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    def step(self) -> None:
        """Emit the next frame synchronously; its timestamp is frame_number / rate_hz seconds."""
        if self.new_frame_listener is None or self.rigid_body_listener is None:
            raise RuntimeError("SyntheticNatNetSource listeners must be set before frames are emitted")
        t: float = self._frame_number / self.rate_hz
        for path in self._paths:
            position, rotation = arena_to_optitrack(arena_pose_on_circle(path, t))
            self.rigid_body_listener(path.body_id, position, rotation, True)
        self.new_frame_listener({"frame_number": self._frame_number, "timestamp": t})
        self._frame_number += 1

    def run(self) -> bool:
        """Start emitting frames on a background thread."""
        if self.new_frame_listener is None or self.rigid_body_listener is None:
            raise RuntimeError("SyntheticNatNetSource listeners must be set before run()")
        self._thread = threading.Thread(target=self._loop, name="synthetic-natnet", daemon=True)
        self._thread.start()
        return True

    def shutdown(self) -> None:
        """Stop emitting frames."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def _loop(self) -> None:
        start: float = self._clock()
        first_frame: int = self._frame_number
        while not self._stop.is_set():
            self.step()
            delay: float = start + (self._frame_number - first_frame) / self.rate_hz - self._clock()
            if delay > 0:
                self._stop.wait(delay)


def add_lists(totals, totals_tmp):
    totals[0] += totals_tmp[0]
    totals[1] += totals_tmp[1]
    totals[2] += totals_tmp[2]
    return totals


def print_configuration(natnet_client):
    natnet_client.refresh_configuration()
    print("Connection Configuration:")
    print("  Client:          %s" % natnet_client.local_ip_address)
    print("  Server:          %s" % natnet_client.server_ip_address)
    print("  Command Port:    %d" % natnet_client.command_port)
    print("  Data Port:       %d" % natnet_client.data_port)

    if natnet_client.use_multicast:
        print("  Using Multicast")
        print("  Multicast Group: %s" % natnet_client.multicast_address)
    else:
        print("  Using Unicast")

    # NatNet Server Info
    application_name = natnet_client.get_application_name()
    nat_net_requested_version = natnet_client.get_nat_net_requested_version()
    nat_net_version_server = natnet_client.get_nat_net_version_server()
    server_version = natnet_client.get_server_version()

    print("  NatNet Server Info")
    print("    Application Name %s" % (application_name))
    print("    NatNetVersion  %d %d %d %d" % (
        nat_net_version_server[0], nat_net_version_server[1], nat_net_version_server[2], nat_net_version_server[3]))
    print(
        "    ServerVersion  %d %d %d %d" % (server_version[0], server_version[1], server_version[2], server_version[3]))
    print("  NatNet Bitstream Requested")
    print("    NatNetVersion  %d %d %d %d" % (nat_net_requested_version[0], nat_net_requested_version[1],
                                              nat_net_requested_version[2], nat_net_requested_version[3]))
    # print("command_socket = %s"%(str(natnet_client.command_socket)))
    # print("data_socket    = %s"%(str(natnet_client.data_socket)))


def print_commands(can_change_bitstream):
    outstring = "Commands:\n"
    outstring += "Return Data from Motive\n"
    outstring += "  s  send data descriptions\n"
    outstring += "  r  resume/start frame playback\n"
    outstring += "  p  pause frame playback\n"
    outstring += "     pause may require several seconds\n"
    outstring += "     depending on the frame data size\n"
    outstring += "Change Working Range\n"
    outstring += "  o  reset Working Range to: start/current/end frame = 0/0/end of take\n"
    outstring += "  w  set Working Range to: start/current/end frame = 1/100/1500\n"
    outstring += "Return Data Display Modes\n"
    outstring += "  j  print_level = 0 supress data description and mocap frame data\n"
    outstring += "  k  print_level = 1 show data description and mocap frame data\n"
    outstring += "  l  print_level = 20 show data description and every 20th mocap frame data\n"
    outstring += "Change NatNet data stream version (Unicast only)\n"
    outstring += "  3  Request 3.1 data stream (Unicast only)\n"
    outstring += "  4  Request 4.1 data stream (Unicast only)\n"
    outstring += "t  data structures self test (no motive/server interaction)\n"
    outstring += "c  show configuration\n"
    outstring += "h  print commands\n"
    outstring += "q  quit\n"
    outstring += "\n"
    outstring += "NOTE: Motive frame playback will respond differently in\n"
    outstring += "       Endpoint, Loop, and Bounce playback modes.\n"
    outstring += "\n"
    outstring += "EXAMPLE: PacketClient [serverIP [ clientIP [ Multicast/Unicast]]]\n"
    outstring += "         PacketClient \"192.168.10.14\" \"192.168.10.14\" Multicast\n"
    outstring += "         PacketClient \"127.0.0.1\" \"127.0.0.1\" u\n"
    outstring += "\n"
    print(outstring)


def request_data_descriptions(s_client):
    # Request the model definitions
    s_client.send_request(s_client.command_socket, s_client.NAT_REQUEST_MODELDEF, "",
                          (s_client.server_ip_address, s_client.command_port))


def test_classes():
    totals = [0, 0, 0]
    print("Test Data Description Classes")
    totals_tmp = DataDescriptions.test_all()
    totals = add_lists(totals, totals_tmp)
    print("")
    print("Test MoCap Frame Classes")
    totals_tmp = MoCapData.test_all()
    totals = add_lists(totals, totals_tmp)
    print("")
    print("All Tests totals")
    print("--------------------")
    print("[PASS] Count = %3.1d" % totals[0])
    print("[FAIL] Count = %3.1d" % totals[1])
    print("[SKIP] Count = %3.1d" % totals[2])


def hub_url(value: str) -> str:
    """argparse type: an http(s) URL with a host, e.g. http://127.0.0.1:3000."""
    try:
        return validate_hub_url(value)
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_args(argv: Sequence[str] | None, settings: Settings) -> argparse.Namespace:
    """Command line: optional NatNet addresses (positional, as in the NatNet sample) plus --simulate and --url."""
    parser = argparse.ArgumentParser(description="Stream OptiTrack rigid bodies to the CoBeART hub.")
    parser.add_argument("server_address", nargs="?", default=settings.network.server_address,
                        help="OptiTrack (Motive) machine address (default from config)")
    parser.add_argument("client_address", nargs="?", default=settings.network.client_address,
                        help="address of this machine (default from config)")
    parser.add_argument("transport", nargs="?", default=None,
                        help="'u...' for unicast, anything else for multicast (default from config)")
    parser.add_argument("--simulate", action="store_true",
                        help="use scripted synthetic bodies instead of a NatNet connection")
    parser.add_argument("--url", type=hub_url, default=settings.hub_url,
                        help=f"hub Socket.IO URL (default from config or $COBEART_SOCKETIO_URL: {settings.hub_url})")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def build_natnet_client(args: argparse.Namespace, settings: Settings) -> NatNetClient:
    """A NatNet client configured from the command line and settings."""
    use_multicast: bool = (
        settings.network.use_multicast if args.transport is None else args.transport[:1].upper() != "U"
    )
    streaming_client = NatNetClient()
    streaming_client.set_client_address(args.client_address)
    streaming_client.set_server_address(args.server_address)
    streaming_client.set_use_multicast(use_multicast)
    return streaming_client


def run_simulated(source: SyntheticNatNetSource, failure: threading.Event) -> None:
    """Run the synthetic source until SIGINT or SIGTERM; exit non-zero if the pipeline fails."""
    stop = threading.Event()

    def _request_stop(signum: int, frame: FrameType | None) -> None:
        logger.info("Received signal %d, stopping", signum)
        stop.set()

    previous_int = signal.signal(signal.SIGINT, _request_stop)
    previous_term = signal.signal(signal.SIGTERM, _request_stop)
    try:
        source.run()
        logger.info("Simulating NatNet frames at %.0f Hz; Ctrl+C to stop", source.rate_hz)
        while not stop.is_set() and not failure.is_set():
            stop.wait(FAILURE_POLL_S)
    finally:
        source.shutdown()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
    if failure.is_set():
        raise SystemExit("ERROR: motion pipeline failed, see the log above")


def _interrupt_main_on_failure(failure: threading.Event) -> None:
    """Wake the main thread out of a blocking input() with SIGINT once the pipeline has failed."""
    failure.wait()
    main_id: int | None = threading.main_thread().ident
    if main_id is not None:
        signal.pthread_kill(main_id, signal.SIGINT)


def start(argv: Sequence[str] | None = None) -> None:
    """Stream rigid bodies from NatNet (or the synthetic source with --simulate) to the hub."""
    try:
        settings: Settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    args = parse_args(argv, settings)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    failure = threading.Event()
    sender = PayloadSender(
        client=socketio.Client(reconnection=False, json=StrictJson),
        tracker=MetricsTracker(max_vel=settings.metrics.max_vel, window_length=settings.metrics.history_window),
        url=args.url,
        framerate=settings.tracking.package_framerate,
        failure=failure,
    )
    logger.info("Hub %s, package rate %.0f Hz, max %d bodies",
                args.url, settings.tracking.package_framerate, settings.tracking.max_num_objects)
    pipeline = FramePipeline(sender, settings.tracking.max_num_objects, failure)
    sender.connect()
    try:
        if args.simulate:
            source = SyntheticNatNetSource(DEFAULT_SYNTHETIC_PATHS, settings.tracking.tracking_framerate)
            pipeline.attach(source)
            run_simulated(source, failure)
        else:
            streaming_client = build_natnet_client(args, settings)
            pipeline.attach(streaming_client)
            run_natnet(streaming_client, failure)
    finally:
        sender.stop()


def run_natnet(streaming_client: NatNetClient, failure: threading.Event) -> None:
    """Start the NatNet client and run its console; exit non-zero if the pipeline fails."""
    # This will run perpetually, and operate on a separate thread.
    if not streaming_client.run():
        raise SystemExit("ERROR: Could not start streaming client.")

    time.sleep(1)
    if streaming_client.connected() is False:
        streaming_client.shutdown()
        raise SystemExit("ERROR: Could not connect properly.  Check that Motive streaming is on.")

    print_configuration(streaming_client)
    print("\n")
    print_commands(streaming_client.can_change_bitstream_version())

    threading.Thread(target=_interrupt_main_on_failure, args=(failure,), name="failure-watch", daemon=True).start()
    try:
        natnet_console(streaming_client)
    except KeyboardInterrupt:
        streaming_client.shutdown()
        if failure.is_set():
            raise SystemExit("ERROR: motion pipeline failed, see the log above")


def natnet_console(streaming_client: NatNetClient) -> None:
    """Interactive NatNet command console (from the OptiTrack NatNet SDK)."""
    is_looping = True
    while is_looping:
        inchars = input('Enter command or (\'h\' for list of commands)\n')
        if len(inchars) > 0:
            c1 = inchars[0].lower()
            if c1 == 'h':
                print_commands(streaming_client.can_change_bitstream_version())
            elif c1 == 'c':
                print_configuration(streaming_client)
            elif c1 == 's':
                request_data_descriptions(streaming_client)
                time.sleep(1)
            elif (c1 == '3') or (c1 == '4'):
                if streaming_client.can_change_bitstream_version():
                    tmp_major = 4
                    tmp_minor = 1
                    if (c1 == '3'):
                        tmp_major = 3
                        tmp_minor = 1
                    return_code = streaming_client.set_nat_net_version(tmp_major, tmp_minor)
                    time.sleep(1)
                    if return_code == -1:
                        print("Could not change bitstream version to %d.%d" % (tmp_major, tmp_minor))
                    else:
                        print("Bitstream version at %d.%d" % (tmp_major, tmp_minor))
                else:
                    print("Can only change bitstream in Unicast Mode")

            elif c1 == 'p':
                sz_command = "TimelineStop"
                return_code = streaming_client.send_command(sz_command)
                time.sleep(1)
                print("Command: %s - return_code: %d" % (sz_command, return_code))
            elif c1 == 'r':
                sz_command = "TimelinePlay"
                return_code = streaming_client.send_command(sz_command)
                print("Command: %s - return_code: %d" % (sz_command, return_code))
            elif c1 == 'o':
                tmpCommands = ["TimelinePlay",
                               "TimelineStop",
                               "SetPlaybackStartFrame,0",
                               "SetPlaybackStopFrame,1000000",
                               "SetPlaybackLooping,0",
                               "SetPlaybackCurrentFrame,0",
                               "TimelineStop"]
                for sz_command in tmpCommands:
                    return_code = streaming_client.send_command(sz_command)
                    print("Command: %s - return_code: %d" % (sz_command, return_code))
                time.sleep(1)
            elif c1 == 'w':
                tmp_commands = ["TimelinePlay",
                                "TimelineStop",
                                "SetPlaybackStartFrame,10",
                                "SetPlaybackStopFrame,1500",
                                "SetPlaybackLooping,0",
                                "SetPlaybackCurrentFrame,100",
                                "TimelineStop"]
                for sz_command in tmp_commands:
                    return_code = streaming_client.send_command(sz_command)
                    print("Command: %s - return_code: %d" % (sz_command, return_code))
                time.sleep(1)
            elif c1 == 't':
                test_classes()

            elif c1 == 'j':
                streaming_client.set_print_level(0)
                print("Showing only received frame numbers and supressing data descriptions")
            elif c1 == 'k':
                streaming_client.set_print_level(1)
                print("Showing every received frame")

            elif c1 == 'l':
                print_level = streaming_client.set_print_level(20)
                print_level_mod = print_level % 100
                if (print_level == 0):
                    print("Showing only received frame numbers and supressing data descriptions")
                elif (print_level == 1):
                    print("Showing every frame")
                elif (print_level_mod == 1):
                    print("Showing every %dst frame" % print_level)
                elif (print_level_mod == 2):
                    print("Showing every %dnd frame" % print_level)
                elif (print_level == 3):
                    print("Showing every %drd frame" % print_level)
                else:
                    print("Showing every %dth frame" % print_level)

            elif c1 == 'q':
                is_looping = False
                streaming_client.shutdown()
                break
            else:
                print("Error: Command %s not recognized" % c1)
            print("Ready...\n")
    print("exiting")


if __name__ == "__main__":
    start()

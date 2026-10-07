import soundcard as sc


class NoAudioDeviceError(RuntimeError):
    """No usable audio input device, or the requested one does not exist."""


def select_audio_device(index: int | None = None):
    """
    Lists all available microphones, including loopback devices, and returns the one at `index`,
    or prompts the user to select one when `index` is None.

    Raises:
        NoAudioDeviceError: no microphone exists, or `index` is out of range.
    """
    print("--- Searching for Audio Input Devices ---")
    microphones = sc.all_microphones(include_loopback=True)
    if not microphones:
        raise NoAudioDeviceError("No microphones found (including loopbacks)")
    if index is not None:
        if not 0 <= index < len(microphones):
            raise NoAudioDeviceError(
                f"Device index {index} is out of range; {len(microphones)} devices found: "
                + ", ".join(f"{i}: {mic.name}" for i, mic in enumerate(microphones))
            )
        print(f"--> Using device: {microphones[index].name}\n")
        return microphones[index]

    print("--- Please Select an Audio Device ---")
    for i, mic in enumerate(microphones):
        print(f"Index {i}: {mic.name}")
    print("---------------------------------------")

    mic_index = None
    while mic_index is None:
        try:
            raw_index = input("Enter the index of the device you want to use: ")
            candidate_index = int(raw_index)
            if 0 <= candidate_index < len(microphones):
                mic_index = candidate_index
            else:
                print("Invalid index. Please choose from the list above.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except (KeyboardInterrupt, EOFError):
            raise NoAudioDeviceError("Device selection cancelled") from None

    selected_mic = microphones[mic_index]
    print(f"--> Using device: {selected_mic.name}\n")
    return selected_mic

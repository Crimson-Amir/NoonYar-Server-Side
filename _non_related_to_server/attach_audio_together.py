from pydub import AudioSegment
import os


def merge_audio_numbers(number, audio_folder, output_folder):
    """
    Merges audio files corresponding to the given number.

    :param number: The number to generate the audio for.
    :param audio_folder: Folder containing audio files.
    :param output_folder: Folder to save the merged audio file.
    """
    str_number = str(number)
    audio_files = []

    if 0 <= number < 10:
        if os.path.exists(os.path.join(audio_folder, f"{number}.mp3")):
            audio_files.append(f"{number}.mp3")

    elif 10 <= number < 100:
        if number % 10 == 0:  # 10, 20, ..., 90
            tens = f"{number}_"
            if os.path.exists(os.path.join(audio_folder, f"{tens}.mp3")):
                audio_files.append(f"{tens}.mp3")
        else:
            tens = str_number[0] + "0_" if len(str_number) > 1 and str_number[0] != "0" else ""
            units = str_number[1] if len(str_number) > 1 and str_number[1] != "0" else ""

            if tens and os.path.exists(os.path.join(audio_folder, f"{tens}.mp3")):
                audio_files.append(f"{tens}.mp3")
            if units and os.path.exists(os.path.join(audio_folder, f"{units}.mp3")):
                audio_files.append(f"{units}.mp3")

    elif len(str_number) == 3:
        hundreds = str_number[0] + "00_"  # e.g., "600_"
        tens_units = int(str_number[1:])
        tens = str_number[1] + "0_" if str_number[1] != "0" else ""
        units = str_number[2] if str_number[2] != "0" else ""

        if os.path.exists(os.path.join(audio_folder, f"{hundreds}.mp3")):
            audio_files.append(f"{hundreds}.mp3")

        if tens_units:
            if os.path.exists(os.path.join(audio_folder, f"{tens_units}.mp3")):
                audio_files.append(f"{tens_units}.mp3")
            else:
                if tens and os.path.exists(os.path.join(audio_folder, f"{tens}.mp3")):
                    audio_files.append(f"{tens}.mp3")
                if units and os.path.exists(os.path.join(audio_folder, f"{units}.mp3")):
                    audio_files.append(f"{units}.mp3")

    # Merge audio files
    merged_audio = AudioSegment.silent(duration=0)
    for file in audio_files:
        file_path = os.path.join(audio_folder, file)
        audio = AudioSegment.from_mp3(file_path)
        merged_audio += audio

    # Export merged audio
    output_path = os.path.join(output_folder, f"{number}.mp3")
    merged_audio.export(output_path, format="mp3")
    print(f"Generated {output_path}")


# Example usage:
# merge_audio_numbers(695, "path/to/audio/files", "path/to/output")

# Example usage:
for a in range(999 + 1):
    merge_audio_numbers(a, "./", "./output")

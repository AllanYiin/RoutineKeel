import wave


def make_wav(path, seconds=12):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b'\0\0' * 16000 * seconds)



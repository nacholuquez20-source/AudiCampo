from datetime import date


class AudioStorage:
    async def save_whatsapp_audio(self, audio_id: str, message_id: str) -> str:
        raise NotImplementedError


class LocalAudioStorage(AudioStorage):
    async def save_whatsapp_audio(self, audio_id: str, message_id: str) -> str:
        if audio_id.startswith("json://"):
            return audio_id
        today = date.today()
        return f"gs://local-dev/audios/{today:%Y/%m/%d}/{message_id}.ogg"

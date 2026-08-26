from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.whatsapp import LocalWhatsAppClient, WhatsAppRealClient, get_whatsapp_client


class TestLocalWhatsAppClient:
    @pytest.mark.asyncio
    async def test_local_client_stores_messages(self):
        client = LocalWhatsAppClient()
        await client.send_text("5491111111111", "Test message")
        assert len(client.sent_messages) == 1
        assert client.sent_messages[0] == ("5491111111111", "Test message")

    @pytest.mark.asyncio
    async def test_local_client_stores_multiple_messages(self):
        client = LocalWhatsAppClient()
        await client.send_text("5491111111111", "Message 1")
        await client.send_text("5492222222222", "Message 2")
        assert len(client.sent_messages) == 2


class TestWhatsAppRealClient:
    @pytest.mark.asyncio
    async def test_real_client_initialization(self):
        client = WhatsAppRealClient("token-123", "phone-456")
        assert client.access_token == "token-123"
        assert client.phone_number_id == "phone-456"
        assert "phone-456" in client.url

    @pytest.mark.asyncio
    async def test_real_client_sends_text_via_api(self):
        """WhatsAppRealClient should send text via API."""
        with patch("httpx.AsyncClient") as mock_client_class:
            # Mock the async context manager
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_client

            client = WhatsAppRealClient("token-123", "phone-456")
            await client.send_text("5491111111111", "Test message")

            # Verify API was called
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "https://graph.instagram.com/v18.0/phone-456/messages" in call_args[0]
            assert call_args[1]["json"]["to"] == "5491111111111"
            assert call_args[1]["json"]["text"]["body"] == "Test message"
            assert call_args[1]["headers"]["Authorization"] == "Bearer token-123"

    @pytest.mark.asyncio
    async def test_real_client_raises_on_api_error(self):
        """WhatsAppRealClient should raise on API error."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock(side_effect=Exception("API error"))

            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            mock_client_class.return_value = mock_client

            client = WhatsAppRealClient("token-123", "phone-456")
            with pytest.raises(Exception):
                await client.send_text("5491111111111", "Test message")


class TestGetWhatsAppClient:
    def test_factory_returns_local_for_none_token(self):
        client = get_whatsapp_client(None, "phone-456")
        assert isinstance(client, LocalWhatsAppClient)

    def test_factory_returns_local_for_none_phone_id(self):
        client = get_whatsapp_client("token-123", None)
        assert isinstance(client, LocalWhatsAppClient)

    def test_factory_returns_real_for_both_params(self):
        client = get_whatsapp_client("token-123", "phone-456")
        assert isinstance(client, WhatsAppRealClient)

    def test_factory_caches_instances(self):
        client1 = get_whatsapp_client("token-123", "phone-456")
        client2 = get_whatsapp_client("token-123", "phone-456")
        assert client1 is client2

    def test_factory_different_tokens_return_different_instances(self):
        client1 = get_whatsapp_client("token-1", "phone-456")
        client2 = get_whatsapp_client("token-2", "phone-456")
        assert client1 is not client2

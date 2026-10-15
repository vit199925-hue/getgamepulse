import asyncio

import httpx
import pytest

from app.assets import cache_cover


def test_verified_cover_is_cached_and_reused(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, content=b"\xff\xd8\xff" + b"image-data", headers={"content-type": "image/jpeg"}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            one = await cache_cover("https://www.metacritic.com/a/img/catalog/cover.jpg", client, tmp_path)
            two = await cache_cover("https://www.metacritic.com/a/img/catalog/cover.jpg", client, tmp_path)
            assert one == two and one.startswith("/media/covers/")

    asyncio.run(run())
    assert len(requests) == 1
    assert len(list(tmp_path.glob("*.jpg"))) == 1


def test_invalid_image_does_not_create_a_cached_file(tmp_path):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, content=b"access denied", headers={"content-type": "image/jpeg"}
                )
            )
        ) as client:
            with pytest.raises(ValueError, match="signature"):
                await cache_cover("https://www.metacritic.com/image.jpg", client, tmp_path)
            with pytest.raises(ValueError, match="HTTPS Metacritic"):
                await cache_cover("https://untrusted.example/image.jpg", client, tmp_path)

    asyncio.run(run())
    assert not list(tmp_path.iterdir())


def test_png_bytes_with_jpeg_mime_are_saved_with_correct_extension(tmp_path):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, content=b"\x89PNG\r\n\x1a\n" + b"image-data", headers={"content-type": "image/jpeg"}
                )
            )
        ) as client:
            result = await cache_cover("https://www.metacritic.com/image.jpg", client, tmp_path)
            assert result.endswith(".png")

    asyncio.run(run())
    assert len(list(tmp_path.glob("*.png"))) == 1

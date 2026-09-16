"""ASGI middleware for Brotli and Gzip HTTP response compression."""

import brotli
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipResponder, IdentityResponder
from starlette.types import ASGIApp, Receive, Scope, Send


class BrotliResponder(IdentityResponder):
    """ASGI responder that streams response chunks through Brotli compression."""

    content_encoding = "br"

    def __init__(self, app: ASGIApp, minimum_size: int, quality: int = 5) -> None:
        super().__init__(app, minimum_size)
        self.compressor = brotli.Compressor(mode=brotli.MODE_TEXT, quality=quality)

    def apply_compression(self, body: bytes, *, more_body: bool) -> bytes:
        chunk = self.compressor.process(body)
        if not more_body:
            chunk += self.compressor.finish()
        return chunk


class CompressionMiddleware:
    """Compress outgoing HTTP responses using Brotli (preferred) or Gzip.

    Respects client Accept-Encoding headers and skips responses smaller than
    `minimum_size` or those that already have a Content-Encoding set.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500, quality: int = 5) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.quality = quality

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        accept = headers.get("Accept-Encoding", "")
        responder: ASGIApp
        if "br" in accept:
            responder = BrotliResponder(self.app, self.minimum_size, quality=self.quality)
        elif "gzip" in accept:
            responder = GZipResponder(self.app, self.minimum_size)
        else:
            responder = IdentityResponder(self.app, self.minimum_size)

        await responder(scope, receive, send)

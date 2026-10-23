"""Manual DeepSeek Web smoke test outside the Kira service.

Usage: DEEPSEEK_WEB_TOKEN=... uv run python scripts/deepseek_web_smoke.py
Never write the token into this file: it would be published and packaged.
"""

import asyncio
import os
import sys
import traceback

from kira.backends.deepseek_web import DeepSeekWebBackend


class FakeAccounts:
    def __init__(self, token: str) -> None:
        self.token = token
        self.state = {"phase": "signed_in", "connected": True}

    async def expire(self, token: str) -> None:
        print("[!] Il server ha rifiutato il token (401). Token scaduto o non valido.", flush=True)

    def status(self) -> dict[str, object]:
        return {"connected": True, "login": self.state}


async def main() -> int:
    token = os.environ.get("DEEPSEEK_WEB_TOKEN", "").strip()
    if not token:
        print("ERRORE: imposta DEEPSEEK_WEB_TOKEN nell'ambiente.", flush=True)
        return 2
    print(f"Token in uso: lunghezza {len(token)}", flush=True)
    backend = DeepSeekWebBackend(FakeAccounts(token))
    try:
        print("1. Test connessione a chat.deepseek.com (users/current)...", flush=True)
        result = await backend.discover()
        print("Risultato discover:", result, flush=True)
        if not result.get("available"):
            print("\n❌ Il server ha rifiutato la richiesta.", flush=True)
            return 1
        print("\n2. Test creazione sessione chat...", flush=True)
        await backend._create_session(token)
        print("Sessione creata con successo.", flush=True)
        print("\n3. Test richiesta PoW Challenge...", flush=True)
        await backend._challenge(token, "/api/v0/chat/completion")
        print("PoW risolto con successo.", flush=True)
        print("\n✅ TUTTO FUNZIONA! Il backend è pronto.", flush=True)
        return 0
    except Exception as exc:
        print(f"\n❌ ERRORE CRITICO: {type(exc).__name__}", flush=True)
        traceback.print_exc()
        return 1
    finally:
        await backend.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

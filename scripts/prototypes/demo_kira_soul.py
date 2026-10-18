"""Print synthetic lifecycle evidence; use a temporary store, never a live profile."""

import json
import tempfile
from pathlib import Path

from kira_soul import Checkpoint, SoulError, SoulStore, assemble, capsule, search


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="kira-soul-demo-") as directory:
        store = SoulStore(Path(directory) / "demo.sqlite")
        try:
            studio = store.create("Kira Studio", "Separate facts from hypotheses. Verify outcomes.", "adaptive")
            atelier = store.create("Kira Atelier", "Explore alternatives and refine useful ideas.")
            store.bind("chat-studio", "project-example", studio)
            store.bind("chat-atelier", "project-example", atelier)
            before = store.start_turn("chat-studio")
            source = store.record_source(studio, "explicit_preference", "Prefer a direct tone.",
                                         preference={"key": "tone", "value": "direct"})
            store.accept(studio, store.propose(studio, source), 1, automatic=True)
            observed = store.record_source(studio, "verified_result", "A small fixture isolated a failure.",
                                           project="project-example")
            candidate = store.propose(studio, observed,
                summary="For similar failures, try a small fixture before a broad test run.", tags=("testing",))
            store.accept(studio, candidate, 2)
            current = store.start_turn("chat-studio")
            after_compaction = assemble(current, Checkpoint(current.native_key, "synthetic-opaque-state"),
                                       request="Continue the testing task", goal={"id": "example-goal"})
            profile = after_compaction["profile"]
            foreign_rejected = False
            try:
                assemble(store.start_turn("chat-atelier"), Checkpoint(current.native_key, "opaque"),
                         request="New task")
            except SoulError:
                foreign_rejected = True
            print(json.dumps({
                "qualification": "offline lifecycle simulation; no live model or real compaction",
                "profiles": ["Kira Studio", "Kira Atelier"],
                "frozen_turn_revision": before.revision,
                "next_turn_revision": current.revision,
                "profile_bytes": profile["bytes"], "profile_tokens": None,
                "profile_preview": profile["text"],
                "capsule_survives_synthetic_compaction": profile == capsule(current),
                "foreign_native_checkpoint_rejected": foreign_rejected,
                "retrieved_lessons": search(current, "testing"),
                "other_soul_lessons": search(store.start_turn("chat-atelier"), "testing"),
                "history": [{"revision": item["revision"], "kind": item["kind"]}
                            for item in store.history(studio)],
            }, indent=2, ensure_ascii=False))
        finally:
            store.close()


if __name__ == "__main__":
    main()

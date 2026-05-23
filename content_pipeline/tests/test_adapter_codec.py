"""Unit tests for the telegram-free parts of bot_pipeline_adapter.

Importing bot_pipeline_adapter must NOT require telegram (lazy imports inside
handlers). These cover the callback codec + keyboard mapping + the 64-byte limit.

Run: python -m unittest content_pipeline.tests.test_adapter_codec -v
"""
from __future__ import annotations

import unittest

import bot_pipeline_adapter as A
from content_pipeline.models import UIIntent, UIAction, UI_SHOW_COST_GATE


class TestCallbackCodec(unittest.TestCase):
    def test_roundtrip_all_actions(self):
        run_id = "a" * 32
        for action in ("approve", "skip", "upload", "confirm_paid",
                       "open_materials", "open_run", "cancel"):
            a = UIAction("label", action, run_id, "avatar", 7)
            cb = A.encode_action(a)
            dec = A.decode_cb(cb)
            self.assertEqual(dec["run_id"], run_id)
            self.assertEqual(dec["stage"], "avatar")
            self.assertEqual(dec["stage_version"], 7)
            self.assertEqual(dec["action"], action)

    def test_callback_under_telegram_64_byte_limit(self):
        run_id = "f" * 32  # uuid4 hex length
        a = UIAction("📥 Скачать материалы", "open_materials", run_id, "avatar", 999)
        cb = A.encode_action(a)
        self.assertLessEqual(len(cb.encode("utf-8")), 64, f"too long: {cb}")

    def test_decode_rejects_foreign_callback(self):
        with self.assertRaises(ValueError):
            A.decode_cb("other:thing:here:1:2")
        with self.assertRaises(ValueError):
            A.decode_cb("sp:tooshort")

    def test_keyboard_spec_maps_actions(self):
        rid = "b" * 32
        intent = UIIntent(
            kind=UI_SHOW_COST_GATE, run_id=rid, title="Платно", body="?",
            actions=[
                UIAction("💳 Запустить платно", "confirm_paid", rid, "avatar", 4, "paid"),
                UIAction("⏭ Пропустить", "skip", rid, "avatar", 4),
            ],
        )
        spec = A.intent_to_keyboard_spec(intent)
        self.assertEqual(len(spec), 2)
        # each row: [(label, callback_data)]
        (lbl0, cb0) = spec[0][0]
        self.assertEqual(lbl0, "💳 Запустить платно")
        self.assertEqual(A.decode_cb(cb0)["action"], "confirm_paid")

    def test_intent_text_builds_title_and_body(self):
        intent = UIIntent(kind="x", title="T", body="B")
        self.assertEqual(A.intent_text(intent), "T\n\nB")
        self.assertEqual(A.intent_text(UIIntent(kind="x", body="only")), "only")


if __name__ == "__main__":
    unittest.main(verbosity=2)

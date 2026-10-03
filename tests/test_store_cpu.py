import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark import store as store_module
from petpark.store import PetStore


class StoreCpuTests(unittest.IsolatedAsyncioTestCase):
    def test_native_encoder_preserves_game_values(self):
        value = {'修士': '云栖', '资源': 2**60, '比例': 0.25,
                 'pets': [None, {'名字': '青岚', '开启': True}], '等级': {'1': 3}}
        expected = json.loads(json.dumps(value, ensure_ascii=False))
        encoded = PetStore._encode_snapshot(value)
        self.assertEqual(json.loads(encoded), expected)
        if store_module._fast_json is not None:
            with patch.object(store_module.json, 'dumps', side_effect=AssertionError('slow path')):
                self.assertEqual(json.loads(PetStore._encode_snapshot(value)), expected)

    def test_oversized_integer_keeps_exact_value(self):
        value = {'资源': 10**100, 'negative': -10**100,
                 'list': [{'coin': 10**90}, 123, None], '正常': {'名字': '青岚'}}
        with patch.object(store_module.json, 'dumps', wraps=json.dumps) as slow:
            self.assertEqual(json.loads(PetStore._encode_snapshot(value)), value)
        if store_module._fast_json is not None:
            # Only the scalar big integers take the slow path, not entire players.
            self.assertEqual(slow.call_count, 3)
            self.assertTrue(all(isinstance(call.args[0], int) for call in slow.call_args_list))

    def test_legacy_non_string_keys_remain_compatible(self):
        value = {1: '等级', False: '关闭', None: '空'}
        self.assertEqual(json.loads(PetStore._encode_snapshot(value)),
                         json.loads(json.dumps(value, ensure_ascii=False)))

    def test_missing_native_dependency_uses_standard_encoder(self):
        with patch.object(store_module, '_fast_json', None):
            self.assertEqual(json.loads(PetStore._encode_snapshot({'名字': '青岚'})), {'名字': '青岚'})

    async def test_temporary_pet_reference_is_restored_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetStore(Path(directory) / 'data.json')
            player = store.get_player('a', 'g')
            first, second = {'name': '第一只'}, {'name': '第二只'}
            player.update(pets=[first, second], active_pet=0, pet=second)
            await store.flush_now()
            self.assertIs(player['pet'], second)
            written = json.loads(store.path.read_bytes())
            self.assertNotIn('pet', next(iter(written['players'].values())))
            old = store.path.read_bytes()
            with patch.object(PetStore, '_encode_snapshot', side_effect=TypeError('bad snapshot')):
                with self.assertRaises(TypeError):
                    await store.flush_now()
            self.assertIs(player['pet'], second)
            self.assertEqual(store.path.read_bytes(), old)

    async def test_save_still_coalesces_and_flush_now_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PetStore(Path(directory) / 'data.json')
            store.SAVE_COALESCE_SEC = 0.02
            with patch.object(store, '_flush', wraps=store._flush) as flush:
                for coin in range(5):
                    store.get_player('a', 'g')['coin'] = coin
                    await store.save()
                self.assertEqual(flush.call_count, 0)
                await store._save_task
                self.assertEqual(flush.call_count, 1)
            store.get_player('a', 'g')['coin'] = 99
            await store.save()
            await store.flush_now()
            self.assertEqual(PetStore(store.path).get_player('a', 'g')['coin'], 99)


if __name__ == '__main__':
    unittest.main()

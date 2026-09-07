import ast
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from qqbot_pet.petpark.store import PetStore


class StoreLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_compact_save_roundtrip_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            store = PetStore(path)
            store.get_player('a', 'g')['coin'] = 123
            await store.save()
            first = path.read_text(encoding='utf-8')
            self.assertNotIn('\n', first)
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 123)
            store.get_player('a', 'g')['coin'] = 456
            await store.save()
            self.assertEqual(json.loads(path.with_suffix('.bak').read_text(encoding='utf-8')),
                             json.loads(first))
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 456)
            self.assertFalse(path.with_suffix('.tmp').exists())

    async def test_failed_validation_preserves_old_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            store = PetStore(path)
            await store.save()
            original = path.read_bytes()
            store.get_player('a', 'g')['coin'] = 987
            with patch('qqbot_pet.petpark.store.json.loads', side_effect=OSError('read failed')):
                with self.assertRaises(OSError):
                    await store.save()
            self.assertEqual(path.read_bytes(), original)


class ApiTimingTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        source = Path(__file__).resolve().parents[2] / 'petbot_framework/core/bot.py'
        owner = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body
                     if isinstance(n, ast.ClassDef) and n.name == 'SkyeBotClient')
        methods = [n for n in owner.body if getattr(n, 'name', '') in
                   {'send_group', 'send_c2c', '_post_timed'}]
        cls = ast.ClassDef(name='Client', bases=[], keywords=[], body=methods, decorator_list=[])
        self.logger = Mock()
        env = dict(time=time, random=random, logger=self.logger)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                     '<send>', 'exec'), env)
        client = env['Client']()
        client.api = SimpleNamespace(post_group_message=AsyncMock(return_value={'id': 'ok'}),
                                     post_c2c_message=AsyncMock(return_value={'id': 'ok'}))
        return client

    async def test_success_preserves_payload_and_response(self):
        client = self.client()
        result = await client.send_group('g', 'private content', msg_id='m')
        self.assertEqual(result, {'id': 'ok'})
        payload = client.api.post_group_message.call_args.kwargs
        self.assertEqual(payload['content'], 'private content')
        self.assertEqual(payload['msg_type'], 0)
        await client.send_c2c('u', 'hello')
        self.assertEqual(self.logger.info.call_count, 2)
        self.assertNotIn('private content', str(self.logger.info.call_args_list))

    async def test_failure_is_logged_and_not_swallowed_or_retried(self):
        client = self.client()
        client.api.post_c2c_message.side_effect = TimeoutError('timeout')
        with self.assertRaises(TimeoutError):
            await client.send_c2c('u', 'hello')
        client.api.post_c2c_message.assert_awaited_once()
        self.assertIn('error', self.logger.info.call_args.args)


if __name__ == '__main__':
    unittest.main()

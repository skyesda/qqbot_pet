import ast
import asyncio
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
            await store.flush_now()  # 「返回即已落盘」的路径是 flush_now（save 改为合并落盘）
            first = path.read_text(encoding='utf-8')
            self.assertNotIn('\n', first)
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 123)
            store.get_player('a', 'g')['coin'] = 456
            await store.flush_now()
            self.assertEqual(json.loads(path.with_suffix('.bak').read_text(encoding='utf-8')),
                             json.loads(first))
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 456)
            self.assertFalse(path.with_suffix('.tmp').exists())

    async def test_failed_validation_preserves_old_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            store = PetStore(path)
            await store.flush_now()
            original = path.read_bytes()
            store.get_player('a', 'g')['coin'] = 987
            # 写后校验已从「读回重新 json.loads」换成字节数比对（O(1)），校验点是
            # _verify_written；失败必须发生在 tmp.replace 之前，旧档原样保留。
            with patch.object(PetStore, '_verify_written', side_effect=OSError('verify failed')):
                with self.assertRaises(OSError):
                    await store.flush_now()
            self.assertEqual(path.read_bytes(), original)


class SaveCoalesceTests(unittest.IsolatedAsyncioTestCase):
    """存档合并：一个窗口内的多次 save() 只落一次盘，且 save() 不阻塞调用方。"""

    def setUp(self):
        # 把合并窗口压到 50ms，测试不必等真实的 250ms
        self.addCleanup(setattr, PetStore, 'SAVE_COALESCE_SEC', PetStore.SAVE_COALESCE_SEC)
        PetStore.SAVE_COALESCE_SEC = 0.05

    async def test_burst_saves_write_disk_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            store = PetStore(path)
            store.get_player('a', 'g')['coin'] = 1
            await store.flush_now()  # 先建磁盘基线，下面数的是新增的写盘次数
            flushes = []
            original = PetStore._flush

            def counting(self):
                flushes.append(1)
                return original(self)

            with patch.object(PetStore, '_flush', counting):
                for coin in range(2, 7):
                    store.get_player('a', 'g')['coin'] = coin
                    await store.save()
                # save() 返回时不该已经写盘（这就是它不再阻塞回复路径的原因）
                self.assertEqual(flushes, [])
                await asyncio.sleep(0.2)  # 等合并窗口到期 + 落盘
            # 5 次 save() 合并成 1 次写盘（合并前是 5 次全量存档）
            self.assertEqual(len(flushes), 1)
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 6)

    async def test_flush_now_bypasses_window(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'data.json'
            store = PetStore(path)
            store.get_player('a', 'g')['coin'] = 7
            await store.save()
            await store.flush_now()  # 不等窗口：停机/清档路径靠它保证已落盘
            self.assertEqual(PetStore(path).get_player('a', 'g')['coin'], 7)
            self.assertFalse(path.with_suffix('.tmp').exists())


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

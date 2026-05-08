import asyncio

import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


@register("astrbot_plugin_simpleverify", "YoungUsing", "新成员入群验证：点击表情完成验证，超时自动移出", "1.0.0")
class SimpleVerify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pending_verify: dict[tuple[str, str], asyncio.Task] = {}
        self._verify_msg_ids: dict[tuple[str, str], str] = {}
        self._client = None

    async def initialize(self):
        try:
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            if platform:
                self._client = platform.get_client()
                if hasattr(self._client, "on_notice"):
                    self._client.on_notice(self._on_notice)
                    logger.info("[SimpleVerify] 已注册 OneBot 通知事件处理器")
                else:
                    logger.warning("[SimpleVerify] 当前协议端不支持通知事件注册，插件可能无法正常工作")
        except Exception as e:
            logger.error(f"[SimpleVerify] 初始化失败: {e}")

    async def _on_notice(self, event: dict):
        notice_type = event.get("notice_type", "")

        if notice_type == "group_increase":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))

            enabled = self.config.get("enable_groups", [])
            if enabled and group_id not in [str(g) for g in enabled]:
                return

            await self._start_verify(group_id, user_id)

        elif notice_type == "notify":
            sub_type = event.get("sub_type", "")
            if sub_type in ("emoji_like", "msg_emoji_like"):
                group_id = str(event.get("group_id", ""))
                user_id = str(event.get("user_id", ""))
                message_id = str(event.get("message_id", ""))
                await self._on_reaction(group_id, user_id, message_id)

    async def _start_verify(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        verify_face = str(self.config.get("verify_face_id", 76))
        timeout = int(self.config.get("timeout", 60))
        text = self.config.get(
            "verify_text",
            "新人验证：请在 {timeout} 秒内点击上方表情完成验证，超时将被移出群聊。",
        )
        text = text.replace("{timeout}", str(timeout))

        msg_segments = [
            {"type": "at", "data": {"qq": user_id}},
            {"type": "text", "data": {"text": " "}},
            {"type": "face", "data": {"id": verify_face}},
            {"type": "text", "data": {"text": "\n" + text}},
        ]

        try:
            result = await self._client.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=msg_segments,
            )
            message_id = str(result.get("message_id", ""))
            self._verify_msg_ids[key] = message_id
            logger.info(f"[SimpleVerify] 已向群 {group_id} 成员 {user_id} 发送验证消息")
        except Exception as e:
            logger.error(f"[SimpleVerify] 发送验证消息失败: {e}")
            return

        if key in self._pending_verify:
            self._pending_verify[key].cancel()

        task = asyncio.create_task(self._verify_timeout(group_id, user_id, timeout))
        self._pending_verify[key] = task

    async def _verify_timeout(self, group_id: str, user_id: str, timeout: int):
        try:
            await asyncio.sleep(timeout)
            await self._kick_user(group_id, user_id)
        except asyncio.CancelledError:
            pass
        finally:
            key = (group_id, user_id)
            self._pending_verify.pop(key, None)
            self._verify_msg_ids.pop(key, None)

    async def _kick_user(self, group_id: str, user_id: str):
        try:
            await self._client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
            logger.info(f"[SimpleVerify] 已将 {user_id} 从群 {group_id} 移出（验证超时）")
        except Exception as e:
            logger.error(f"[SimpleVerify] 移出用户 {user_id} 失败: {e}")

    async def _on_reaction(self, group_id: str, user_id: str, message_id: str):
        key = (group_id, user_id)
        expected_msg_id = self._verify_msg_ids.get(key)
        if expected_msg_id and message_id == expected_msg_id:
            await self._verify_success(group_id, user_id)

    async def _verify_success(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        message_id = self._verify_msg_ids.get(key)
        if not message_id:
            return

        success_face = str(self.config.get("success_face_id", 78))

        try:
            await self._client.api.call_action(
                "set_msg_emoji_like",
                message_id=message_id,
                emoji_id=success_face,
            )
            logger.info(f"[SimpleVerify] {user_id} 在群 {group_id} 验证成功")
        except Exception as e:
            logger.warning(f"[SimpleVerify] 贴成功表情失败（当前协议端可能不支持）: {e}")

        if key in self._pending_verify:
            self._pending_verify[key].cancel()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群聊消息，检测验证用户是否通过发送表情来完成验证"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        key = (group_id, user_id)

        if key not in self._pending_verify:
            return

        verify_face = int(self.config.get("verify_face_id", 76))
        for comp in event.get_messages():
            if isinstance(comp, Comp.Face) and str(comp.id) == str(verify_face):
                await self._verify_success(group_id, user_id)
                event.stop_event()
                return

    async def terminate(self):
        for task in self._pending_verify.values():
            task.cancel()
        self._pending_verify.clear()
        self._verify_msg_ids.clear()

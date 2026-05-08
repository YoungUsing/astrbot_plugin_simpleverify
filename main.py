import asyncio

import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


@register("astrbot_plugin_simpleverify", "YoungUsing", "新成员入群验证：贴表情完成验证，超时自动移出", "1.0.2")
class SimpleVerify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pending_verify: dict[tuple[str, str], asyncio.Task] = {}
        self._verify_msg_ids: dict[tuple[str, str], str] = {}
        self._group_umo: dict[str, str] = {}
        self._client = None

    async def initialize(self):
        logger.info("[SimpleVerify] 插件初始化中...")
        try:
            platform = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            if platform:
                self._client = platform.get_client()
                logger.info(f"[SimpleVerify] 获取到 OneBot 客户端: {type(self._client).__name__}")
                if hasattr(self._client, "on_notice"):
                    self._client.on_notice(self._on_notice)
                    logger.info("[SimpleVerify] 已注册 OneBot 通知事件处理器")
                else:
                    logger.warning("[SimpleVerify] 当前协议端不支持 on_notice 注册")
        except Exception as e:
            logger.error(f"[SimpleVerify] 初始化失败: type={type(e).__name__}, {e}")
        logger.info(
            f"[SimpleVerify] 配置: verify_face_id={self.config.get('verify_face_id', 76)}, "
            f"success_face_id={self.config.get('success_face_id', 78)}, "
            f"emoji_type={self.config.get('emoji_type', 'face')}, "
            f"timeout={self.config.get('timeout', 60)}s, "
            f"enable_groups={self.config.get('enable_groups', [])}"
        )

    # ── OneBot 通知事件 ────────────────────────────────────────

    async def _on_notice(self, event: dict):
        logger.info(f"[SimpleVerify] 收到通知事件: {event}")
        notice_type = event.get("notice_type", "")

        if notice_type == "group_increase":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            self_id = str(event.get("self_id", ""))
            logger.info(f"[SimpleVerify] 检测到新成员入群: group_id={group_id}, user_id={user_id}")

            # 确保 UMO 缓存可用
            if group_id not in self._group_umo:
                self._group_umo[group_id] = f"aiocqhttp:{self_id}:{group_id}"

            enabled = self.config.get("enable_groups", [])
            if enabled:
                enabled_str = [str(g) for g in enabled]
                if group_id not in enabled_str:
                    logger.info(f"[SimpleVerify] 群 {group_id} 不在启用列表中，跳过")
                    return

            await self._start_verify(group_id, user_id)

        elif notice_type == "notify":
            sub_type = event.get("sub_type", "")
            logger.info(f"[SimpleVerify] 通知子类型: {sub_type}")
            if sub_type in ("emoji_like", "msg_emoji_like", "emoji_reaction"):
                group_id = str(event.get("group_id", ""))
                user_id = str(event.get("user_id", ""))
                message_id = str(event.get("message_id", ""))
                logger.info(
                    f"[SimpleVerify] 贴表情通知: sub_type={sub_type}, "
                    f"group_id={group_id}, user_id={user_id}, message_id={message_id}"
                )
                await self._handle_reaction(group_id, user_id, message_id)

    # ── 验证流程 ──────────────────────────────────────────────

    async def _send_verify_msg(self, group_id: str, user_id: str) -> str | None:
        verify_face = str(self.config.get("verify_face_id", 76))
        timeout = int(self.config.get("timeout", 60))
        text = self.config.get(
            "verify_text",
            "新人验证：请在 {timeout} 秒内长按上方表情并「贴表情」完成验证，超时将被移出群聊。",
        )
        text = text.replace("{timeout}", str(timeout))

        chain = MessageChain().at(user_id).text(" ").face(int(verify_face)).text("\n" + text)

        umo = self._group_umo.get(group_id)
        if not umo:
            logger.error(f"[SimpleVerify] 未找到群 {group_id} 的 unified_msg_origin")
            return None

        try:
            result = await self.context.send_message(umo, chain)
            logger.info(f"[SimpleVerify] send_message 返回: {result}")
            if isinstance(result, dict):
                return str(result.get("message_id", ""))
            return None
        except Exception as e:
            logger.error(f"[SimpleVerify] 发送验证消息失败: type={type(e).__name__}, {e}")
            return None

    async def _start_verify(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        timeout = int(self.config.get("timeout", 60))

        logger.info(f"[SimpleVerify] 开始验证: group_id={group_id}, user_id={user_id}, timeout={timeout}s")

        message_id = await self._send_verify_msg(group_id, user_id)
        if not message_id:
            logger.error("[SimpleVerify] 验证消息发送失败，放弃本次验证")
            return

        self._verify_msg_ids[key] = message_id

        if key in self._pending_verify:
            logger.info(f"[SimpleVerify] 取消 user_id={user_id} 的旧验证任务")
            self._pending_verify[key].cancel()

        task = asyncio.create_task(self._verify_timeout(group_id, user_id, timeout))
        self._pending_verify[key] = task
        logger.info(f"[SimpleVerify] 超时任务已创建，剩余 {timeout}s")

    async def _verify_timeout(self, group_id: str, user_id: str, timeout: int):
        try:
            await asyncio.sleep(timeout)
            logger.info(f"[SimpleVerify] 验证超时: group_id={group_id}, user_id={user_id}")
            await self._kick_user(group_id, user_id)
        except asyncio.CancelledError:
            logger.info(f"[SimpleVerify] 验证任务被取消（用户已验证成功）: user_id={user_id}")
        finally:
            key = (group_id, user_id)
            self._pending_verify.pop(key, None)
            self._verify_msg_ids.pop(key, None)

    async def _kick_user(self, group_id: str, user_id: str):
        try:
            result = await self._client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
            logger.info(f"[SimpleVerify] set_group_kick 返回: {result}")
        except Exception as e:
            logger.error(f"[SimpleVerify] 移出用户 {user_id} 失败: type={type(e).__name__}, {e}")

    # ── 贴表情验证 ────────────────────────────────────────────

    async def _handle_reaction(self, group_id: str, user_id: str, message_id: str):
        key = (group_id, user_id)
        expected_msg_id = self._verify_msg_ids.get(key)
        logger.info(
            f"[SimpleVerify] 贴表情匹配: expected_msg_id={expected_msg_id}, "
            f"received_msg_id={message_id}, user_id={user_id}"
        )
        if expected_msg_id and message_id == expected_msg_id:
            logger.info(f"[SimpleVerify] 贴表情匹配成功，user_id={user_id} 验证通过")
            await self._verify_success(group_id, user_id)
        else:
            logger.info(f"[SimpleVerify] 贴表情不匹配或用户不在验证列表中，跳过")

    async def _verify_success(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        message_id = self._verify_msg_ids.get(key)
        if not message_id:
            logger.warning(f"[SimpleVerify] 未找到 user_id={user_id} 的验证消息ID")
            self._cancel_verify(key)
            return

        success_face = str(self.config.get("success_face_id", 78))
        emoji_type = str(self.config.get("emoji_type", "face"))
        logger.info(f"[SimpleVerify] 验证成功: user_id={user_id}, 贴成功表情 id={success_face} type={emoji_type}")

        try:
            await self._client.api.call_action(
                "set_msg_emoji_like",
                message_id=message_id,
                emoji_id=success_face,
                emoji_type=emoji_type,
                set=True,
            )
            logger.info(f"[SimpleVerify] 成功表情已贴: message_id={message_id}")
        except Exception as e:
            logger.warning(f"[SimpleVerify] 贴成功表情失败: type={type(e).__name__}, {e}")

        self._cancel_verify(key)

    def _cancel_verify(self, key: tuple[str, str]):
        if key in self._pending_verify:
            self._pending_verify[key].cancel()
            logger.debug(f"[SimpleVerify] 验证任务已取消: key={key}")

    # ── 指令 ──────────────────────────────────────────────────

    @filter.command("verify")
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def manual_verify(self, event: AstrMessageEvent):
        """手动对新成员发起验证：/verify @某人"""
        group_id = event.get_group_id()
        target_user_id = None

        for comp in event.get_messages():
            if isinstance(comp, Comp.At):
                target_user_id = str(comp.qq)
                break

        if not target_user_id:
            logger.info(f"[SimpleVerify] /verify 指令未找到 @目标，发送者={event.get_sender_id()}")
            yield event.plain_result("[SimpleVerify] 请 @ 要验证的群成员，例如：/verify @某人")
            return

        self._group_umo[group_id] = event.unified_msg_origin

        logger.info(
            f"[SimpleVerify] 管理员 {event.get_sender_id()} 手动发起验证: "
            f"target={target_user_id}, group={group_id}"
        )
        await self._start_verify(group_id, target_user_id)
        event.stop_event()

    # ── 群消息兜底 ────────────────────────────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """缓存 UMO，并兜底检测用户发送验证表情的消息"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        key = (group_id, user_id)

        self._group_umo[group_id] = event.unified_msg_origin

        if key not in self._pending_verify:
            return

        # 兜底：用户发送了验证表情作为消息
        verify_face = int(self.config.get("verify_face_id", 76))
        for comp in event.get_messages():
            if isinstance(comp, Comp.Face) and str(comp.id) == str(verify_face):
                logger.info(f"[SimpleVerify] 用户 {user_id} 通过发送表情消息完成验证（兜底）")
                await self._verify_success(group_id, user_id)
                event.stop_event()
                return

    # ── 卸载 ──────────────────────────────────────────────────

    async def terminate(self):
        logger.info("[SimpleVerify] 插件卸载中，清理验证任务...")
        for key, task in self._pending_verify.items():
            logger.info(f"[SimpleVerify] 取消验证任务: {key}")
            task.cancel()
        self._pending_verify.clear()
        self._verify_msg_ids.clear()
        logger.info("[SimpleVerify] 插件已卸载")

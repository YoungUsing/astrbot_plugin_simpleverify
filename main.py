import asyncio
import random

import astrbot.api.message_components as Comp
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig


DEFAULT_RECAPTCHA_EMOJI_POOL = [
    {"id": 0, "desc": "惊讶"},
    {"id": 54, "desc": "刀"},
    {"id": 74, "desc": "太阳"},
    {"id": 109, "desc": "月亮"},
    {"id": 60, "desc": "咖啡"},
    {"id": 53, "desc": "蛋糕"},
    {"id": 59, "desc": "便便"},
    {"id": 66, "desc": "握手"},
    {"id": 76, "desc": "赞"},
]


@register("astrbot_plugin_simpleverify", "YoungUsing", "新成员入群验证：三种验证模式，超时自动移出", "1.1.3")
class SimpleVerify(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._pending_verify: dict[tuple[str, str], asyncio.Task] = {}
        self._verify_msg_ids: dict[tuple[str, str], str] = {}
        self._expected_face: dict[tuple[str, str], int] = {}
        self._verify_face_ids: dict[tuple[str, str], list[int]] = {}
        self._group_umo: dict[str, str] = {}
        self._client = None
        self._self_id: str = ""
        self._shutdown = False
        self._verify_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._seen_reactions: set[tuple[str, str, str]] = set()

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
                login_info = await self._client.api.call_action("get_login_info")
                self._self_id = str(login_info.get("user_id", "")) if isinstance(login_info, dict) else ""
        except Exception as e:
            logger.error(f"[SimpleVerify] 初始化失败: type={type(e).__name__}, {e}")

        logger.info(
            f"[SimpleVerify] 初始化完成 self_id={self._self_id} "
            f"method={self.config.get('verify_method', '低')} "
            f"timeout={self.config.get('timeout', 60)}s"
        )

    # ── OneBot 通知事件 ────────────────────────────────────────

    @staticmethod
    def _extract_emoji_id(event: dict) -> str | None:
        if "emoji_id" in event:
            return str(event["emoji_id"])
        likes = event.get("likes", [])
        if likes and isinstance(likes, list) and len(likes) > 0:
            first = likes[0]
            if isinstance(first, dict):
                return str(first.get("emoji_id", ""))
        return None

    async def _on_notice(self, event: dict):
        if self._shutdown:
            return
        notice_type = event.get("notice_type", "")

        if notice_type == "group_increase":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            self_id = str(event.get("self_id", ""))
            logger.info(f"[SimpleVerify] 新成员入群: group_id={group_id}, user_id={user_id}")

            if group_id not in self._group_umo:
                self._group_umo[group_id] = f"aiocqhttp:{self_id}:{group_id}"

            enabled = self.config.get("enable_groups", [])
            if enabled and group_id not in [str(g) for g in enabled]:
                logger.info(f"[SimpleVerify] 群 {group_id} 不在启用列表中，跳过")
                return

            await self._start_verify(group_id, user_id)

        elif notice_type == "group_msg_emoji_like":
            group_id = str(event.get("group_id", ""))
            user_id = str(event.get("user_id", ""))
            message_id = str(event.get("message_id", ""))
            is_add = event.get("is_add", True)

            if self._self_id and user_id == self._self_id:
                return
            if not is_add:
                return

            emoji_id = self._extract_emoji_id(event)

            dedup_key = (group_id, user_id, message_id, emoji_id)
            if dedup_key in self._seen_reactions:
                return
            if len(self._seen_reactions) > 500:
                self._seen_reactions.clear()
            self._seen_reactions.add(dedup_key)
            await self._handle_reaction(group_id, user_id, message_id, emoji_id)

    # ── 表情选取 & 放置 ──────────────────────────────────────

    def _get_random_emoji_set(self) -> tuple[int, str, str, list[int]]:
        pool = self.config.get("recaptcha_emoji_pool", DEFAULT_RECAPTCHA_EMOJI_POOL)
        success_face = int(self.config.get("success_face_id", 78))
        pool = [e for e in pool if e["id"] != success_face]

        count = min(int(self.config.get("challenge_count", 4)), len(pool))
        if count < 1:
            count = 1

        selected = random.sample(pool, count)
        target = selected[0]
        random.shuffle(selected)
        face_ids = [e["id"] for e in selected]
        return target["id"], target["desc"], target.get("riddle", ""), face_ids

    async def _place_emoji_reactions(self, message_id: str, face_ids: list[int]):
        for fid in face_ids:
            try:
                await self._client.api.call_action(
                    "set_msg_emoji_like",
                    message_id=message_id,
                    emoji_id=str(fid),
                )
            except Exception as e:
                logger.warning(f"[SimpleVerify] 贴表情失败 face={fid}: type={type(e).__name__}, {e}")
            await asyncio.sleep(0.1)

    # ── 验证流程 ──────────────────────────────────────────────

    async def _send_verify_msg(self, group_id: str, user_id: str,
                                extra: str = "") -> str | None:
        timeout = int(self.config.get("timeout", 60))
        method = self.config.get("verify_method", "低")

        if method == "低":
            text = self.config.get("verify_text", "新人验证：请在 {timeout} 秒内点击下方按钮完成验证，超时将被移出群聊。")
            prompt = text.replace("{timeout}", str(timeout))
        elif method in ("高", "极高"):
            text = self.config.get("verify_text_high", "新人验证：请在 {timeout} 秒内点击 {extra}，超时将被移出群聊。")
            prompt = text.replace("{timeout}", str(timeout)).replace("{extra}", extra)
        elif method == "中":
            text = self.config.get("verify_text_medium", "新人验证：请在 {timeout} 秒内点击 {extra} 完成验证，超时将被移出群聊。")
            prompt = text.replace("{timeout}", str(timeout)).replace("{extra}", extra)
        else:
            text = self.config.get("verify_text", "新人验证：请在 {timeout} 秒内点击下方按钮完成验证，超时将被移出群聊。")
            prompt = text.replace("{timeout}", str(timeout))

        cq_message = f"[CQ:at,qq={user_id}]\n{prompt}"

        try:
            result = await self._client.api.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=cq_message,
            )
        except Exception as e:
            logger.error(f"[SimpleVerify] 发送验证消息失败: type={type(e).__name__}, {e}")
            return None

        message_id = str(result.get("message_id", "")) if isinstance(result, dict) else None
        if not message_id:
            logger.error(f"[SimpleVerify] 未能提取 message_id: {result}")
        return message_id

    async def _start_verify(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        lock = self._verify_locks.setdefault(key, asyncio.Lock())
        async with lock:
            await self._do_start_verify(group_id, user_id, key)

    async def _do_start_verify(self, group_id: str, user_id: str, key: tuple[str, str]):
        timeout = int(self.config.get("timeout", 60))
        method = self.config.get("verify_method", "低")

        logger.info(
            f"[SimpleVerify] 开始验证: group_id={group_id}, user_id={user_id}, "
            f"method={method}, timeout={timeout}s"
        )

        target_face_id: int | None = None
        face_ids: list[int] = []
        extra = ""

        if method == "低":
            target_face_id = int(self.config.get("verify_face_id", 76))
        elif method in ("高", "极高"):
            target_id, target_desc, target_riddle, face_ids = self._get_random_emoji_set()
            target_face_id = target_id
            if method == "极高" and target_riddle:
                extra = f"点击下方{target_riddle}的表情"
            else:
                extra = f"点击表示「{target_desc}」的表情完成验证"
            self._expected_face[key] = target_id
        elif method == "中":
            # 纯随机 face ID，无需表情池和描述
            count = max(int(self.config.get("challenge_count", 4)), 1)
            success_face = int(self.config.get("success_face_id", 78))
            target_id = random.randint(0, 170)
            while target_id == success_face:
                target_id = random.randint(0, 170)
            face_ids = [target_id]
            while len(face_ids) < count:
                fid = random.randint(0, 170)
                if fid not in face_ids and fid != success_face:
                    face_ids.append(fid)
            random.shuffle(face_ids)
            target_face_id = target_id
            extra = f"[CQ:face,id={target_id}]"
            self._expected_face[key] = target_id

        message_id = await self._send_verify_msg(group_id, user_id, extra)
        if not message_id:
            logger.error("[SimpleVerify] 验证消息发送失败，放弃本次验证")
            self._expected_face.pop(key, None)
            return

        self._verify_msg_ids[key] = message_id

        old_task = self._pending_verify.pop(key, None)
        if old_task:
            old_task.cancel()

        task = asyncio.create_task(self._verify_timeout(group_id, user_id, timeout))
        self._pending_verify[key] = task

        if method == "低":
            try:
                await self._client.api.call_action(
                    "set_msg_emoji_like",
                    message_id=message_id,
                    emoji_id=str(target_face_id),
                )
                self._verify_face_ids[key] = [target_face_id]
            except Exception as e:
                logger.warning(f"[SimpleVerify] 贴验证表情失败: type={type(e).__name__}, {e}")
        else:
            await self._place_emoji_reactions(message_id, face_ids)
            self._verify_face_ids[key] = face_ids

    async def _verify_timeout(self, group_id: str, user_id: str, timeout: int):
        key = (group_id, user_id)
        this_task = asyncio.current_task()
        try:
            await asyncio.sleep(timeout)
            if key not in self._verify_msg_ids:
                return
            logger.info(f"[SimpleVerify] 验证超时: group_id={group_id}, user_id={user_id}")

            message_id = self._verify_msg_ids.get(key, "")
            _, detail = await self._kick_user(group_id, user_id)

            if message_id:
                reply_text = f"[CQ:reply,id={message_id}]验证失败：{detail}"
                try:
                    await self._client.api.call_action(
                        "send_group_msg",
                        group_id=int(group_id),
                        message=reply_text,
                    )
                    logger.info(f"[SimpleVerify] 失败通知已发送: {detail}")
                except Exception as e:
                    logger.warning(f"[SimpleVerify] 发送失败通知失败: type={type(e).__name__}, {e}")
        except asyncio.CancelledError:
            logger.info(f"[SimpleVerify] 验证任务被取消（用户已验证成功）: user_id={user_id}")
        finally:
            if self._pending_verify.get(key) is this_task:
                self._pending_verify.pop(key, None)
            self._verify_msg_ids.pop(key, None)
            self._expected_face.pop(key, None)
            self._verify_face_ids.pop(key, None)

    async def _kick_user(self, group_id: str, user_id: str) -> tuple[bool, str]:
        try:
            await self._client.api.call_action(
                "set_group_kick",
                group_id=int(group_id),
                user_id=int(user_id),
                reject_add_request=False,
            )
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            logger.error(f"[SimpleVerify] set_group_kick 异常: {detail}")
            if "permission" in str(e).lower() or "auth" in str(e).lower():
                return False, "权限不足，需管理员手动处理"
            return False, detail

        try:
            await self._client.api.call_action(
                "get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
            )
            return False, "踢出失败，可能权限不足，需管理员手动处理"
        except Exception:
            return True, "已踢"

    # ── 贴表情验证 ────────────────────────────────────────────

    async def _handle_reaction(self, group_id: str, user_id: str,
                                message_id: str, emoji_id: str | None):
        key = (group_id, user_id)
        expected_msg_id = self._verify_msg_ids.get(key)
        if not expected_msg_id or message_id != expected_msg_id:
            return

        method = self.config.get("verify_method", "低")

        if method == "低":
            logger.info(f"[SimpleVerify] 贴表情匹配成功，user_id={user_id} 验证通过")
        else:
            expected_face = self._expected_face.get(key)
            if expected_face is None or emoji_id is None:
                return
            if str(emoji_id) != str(expected_face):
                logger.info(
                    f"[SimpleVerify] 用户点击了错误的表情: "
                    f"expected={expected_face}, got={emoji_id}"
                )
                return
            logger.info(f"[SimpleVerify] 正确表情匹配成功，user_id={user_id} 验证通过")

        await self._verify_success(group_id, user_id)

    async def _verify_success(self, group_id: str, user_id: str):
        key = (group_id, user_id)
        self._cancel_verify(key)
        message_id = self._verify_msg_ids.pop(key, None)
        if not message_id:
            return

        logger.info(f"[SimpleVerify] 验证成功: user_id={user_id}")

        # 移除所有干扰表情
        face_ids = self._verify_face_ids.pop(key, [])
        for fid in face_ids:
            try:
                await self._client.api.call_action(
                    "set_msg_emoji_like",
                    message_id=message_id,
                    emoji_id=str(fid),
                    set=False,
                )
            except Exception:
                pass
            await asyncio.sleep(0.05)

        success_face = str(self.config.get("success_face_id", 78))
        try:
            await self._client.api.call_action(
                "set_msg_emoji_like",
                message_id=message_id,
                emoji_id=success_face,
            )
        except Exception as e:
            logger.warning(f"[SimpleVerify] 贴成功表情失败: type={type(e).__name__}, {e}")

    def _cancel_verify(self, key: tuple[str, str]):
        if key in self._pending_verify:
            self._pending_verify[key].cancel()
            logger.debug(f"[SimpleVerify] 验证任务已取消: key={key}")
        self._expected_face.pop(key, None)
        self._verify_face_ids.pop(key, None)

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
                qq = str(comp.qq)
                if qq != self._self_id:
                    target_user_id = qq
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

    # ── 群消息兜底（仅低模式） ────────────────────

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """缓存 UMO，低模式下兜底检测用户发送验证表情的消息。"""
        group_id = event.get_group_id()
        user_id = event.get_sender_id()
        key = (group_id, user_id)

        self._group_umo[group_id] = event.unified_msg_origin

        if key not in self._pending_verify:
            return

        if self.config.get("verify_method", "低") != "低":
            return

        verify_face = int(self.config.get("verify_face_id", 76))
        for comp in event.get_messages():
            if isinstance(comp, Comp.Face) and str(comp.id) == str(verify_face):
                logger.info(f"[SimpleVerify] 用户 {user_id} 通过发送表情消息完成验证（兜底）")
                await self._verify_success(group_id, user_id)
                event.stop_event()
                return

    # ── 卸载 ──────────────────────────────────────────────────

    async def terminate(self):
        self._shutdown = True
        if self._client and hasattr(self._client, "off_notice"):
            try:
                self._client.off_notice(self._on_notice)
            except Exception:
                pass
        self._client = None
        for task in self._pending_verify.values():
            task.cancel()
        self._pending_verify.clear()
        self._verify_msg_ids.clear()
        self._expected_face.clear()
        self._verify_face_ids.clear()
        self._group_umo.clear()
        self._verify_locks.clear()
        self._seen_reactions.clear()

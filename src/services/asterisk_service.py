"""
Asterisk AMI Servisi
Qo'ng'iroqlarni boshqarish va kuzatish
"""

import logging
import asyncio
import re
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class CallStatus(Enum):
    """Qo'ng'iroq holatlari"""
    PENDING = "pending"
    ORIGINATING = "originating"
    RINGING = "ringing"
    ANSWERED = "answered"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    HANGUP = "hangup"
    CANCELLED = "cancelled"  # Buyurtma statusi o'zgarganda


@dataclass
class CallResult:
    """Qo'ng'iroq natijasi"""
    status: CallStatus
    duration: int = 0
    dial_status: str = ""
    channel: str = ""
    error: str = ""

    @property
    def is_answered(self) -> bool:
        return self.status == CallStatus.ANSWERED

    @property
    def is_failed(self) -> bool:
        return self.status in (CallStatus.FAILED, CallStatus.NO_ANSWER, CallStatus.BUSY)


class AsteriskAMI:
    """
    Asterisk Manager Interface (AMI) Client

    Qo'ng'iroq boshlash va kuzatish
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5038,
        username: str = "autodialer",
        password: str = "",
        wsl_sounds_path: str = "/var/lib/asterisk/sounds/autodialer"
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.wsl_sounds_path = wsl_sounds_path

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._action_id = 0
        self._pending_actions: Dict[str, asyncio.Future] = {}
        self._event_handlers: Dict[str, Callable] = {}
        self._read_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

        logger.info(f"Asterisk AMI yaratildi: {host}:{port}")

    async def connect(self) -> bool:
        """AMI ga ulanish"""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )

            # Welcome xabarini o'qish
            welcome = await self._reader.readline()
            logger.debug(f"AMI Welcome: {welcome.decode().strip()}")

            # Login
            login_success = await self._login()
            if not login_success:
                return False

            self._connected = True

            # Event o'qish taskini boshlash
            self._read_task = asyncio.create_task(self._read_events())

            # Ping taskini boshlash (har 30 soniyada keepalive)
            self._ping_task = asyncio.create_task(self._ping_loop())

            logger.info("AMI ulanish muvaffaqiyatli")
            return True

        except Exception as e:
            logger.error(f"AMI ulanish xatosi: {e}")
            return False

    async def disconnect(self):
        """AMI dan uzilish"""
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._writer:
            try:
                await self._send_action("Logoff")
            except Exception as e:
                logger.debug(f"Logoff xatosi (e'tiborsiz): {e}")
            self._writer.close()
            await self._writer.wait_closed()

        self._connected = False
        logger.info("AMI uzildi")

    async def reconnect(self) -> bool:
        """AMI ga qayta ulanish"""
        logger.info("AMI qayta ulanish...")
        # Eski ulanishni tozalash
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._writer:
            try:
                self._writer.close()
            except Exception as e:
                logger.debug(f"Writer yopish xatosi (e'tiborsiz): {e}")
        self._connected = False
        self._reader = None
        self._writer = None
        # Yangi ulanish
        return await self.connect()

    def _validate_and_clean_phone(self, phone: str) -> str:
        """
        Telefon raqamini validatsiya qilish va tozalash

        Args:
            phone: Telefon raqami (+998901234567, 998901234567, 901234567 formatida)

        Returns:
            Tozalangan raqam (faqat raqamlar)

        Raises:
            ValueError: Noto'g'ri format
        """
        if not phone:
            raise ValueError("Telefon raqami bo'sh bo'lishi mumkin emas")

        # Barcha bo'sh joylar va maxsus belgilarni olib tashlash
        clean = re.sub(r'[^\d]', '', phone)

        # O'zbekiston raqamlarini validatsiya qilish
        # 998901234567 (12 raqam) yoki 901234567 (9 raqam) formatida bo'lishi kerak
        if len(clean) == 9:
            # 9 raqamli format - 998 ni qo'shish
            clean = f"998{clean}"
        elif len(clean) == 12:
            # 12 raqamli format - 998 bilan boshlanishi kerak
            if not clean.startswith("998"):
                raise ValueError(f"Noto'g'ri mamlakat kodi: {phone}. Faqat O'zbekiston raqamlari (+998)")
        else:
            raise ValueError(f"Noto'g'ri raqam uzunligi: {phone}. 9 yoki 12 raqam bo'lishi kerak")

        # O'zbekiston operator kodlarini tekshirish (90, 91, 93, 94, 95, 97, 98, 99, 33, 88)
        operator_code = clean[3:5]
        valid_operators = ['90', '91', '93', '94', '95', '97', '98', '99', '33', '88']

        if operator_code not in valid_operators:
            raise ValueError(f"Noto'g'ri operator kodi: {operator_code}. Telefon: {phone}")

        # Final validatsiya - faqat raqamlar
        if not clean.isdigit():
            raise ValueError(f"Raqamda faqat sonlar bo'lishi kerak: {phone}")

        logger.debug(f"Telefon validatsiya: {phone} -> {clean}")
        return clean

    async def _login(self) -> bool:
        """AMI login"""
        if not self._writer or not self._reader:
            return False

        # Login xabarini yuborish
        self._action_id += 1
        action_id = str(self._action_id)

        message = f"Action: Login\r\nActionID: {action_id}\r\nUsername: {self.username}\r\nSecret: {self.password}\r\n\r\n"
        self._writer.write(message.encode())
        await self._writer.drain()

        # Javobni to'g'ridan-to'g'ri o'qish
        try:
            buffer = ""
            while True:
                data = await asyncio.wait_for(self._reader.read(4096), timeout=10)
                if not data:
                    break
                buffer += data.decode()

                if "\r\n\r\n" in buffer:
                    # Response ni parse qilish
                    for line in buffer.split("\r\n"):
                        if line.startswith("Response:"):
                            response_value = line.split(": ", 1)[1] if ": " in line else ""
                            if response_value == "Success":
                                logger.debug("AMI login muvaffaqiyatli")
                                return True
                            else:
                                logger.error(f"AMI login rad etildi: {response_value}")
                                return False
                    break

        except asyncio.TimeoutError:
            logger.error("AMI login timeout")
            return False
        except Exception as e:
            logger.error(f"AMI login xatosi: {e}")
            return False

        return False

    async def _ping_loop(self):
        """AMI ulanishni faol tutish uchun ping yuborish"""
        while self._connected:
            try:
                await asyncio.sleep(10)  # Har 10 soniyada (AMI idle timeout dan oldin)
                if self._connected and self._writer:
                    await self._send_action("Ping")
                    logger.debug("AMI Ping yuborildi")
            except asyncio.CancelledError:
                logger.debug("Ping loop bekor qilindi")
                break
            except Exception as e:
                logger.warning(f"Ping xatosi: {e}")

    async def _send_action(self, action: str, **params) -> Optional[Dict]:
        """AMI action yuborish"""
        if not self._writer:
            return None

        self._action_id += 1
        action_id = str(self._action_id)

        # Action yaratish
        lines = [f"Action: {action}", f"ActionID: {action_id}"]
        for key, value in params.items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append("")

        message = "\r\n".join(lines)

        # Future yaratish
        future = asyncio.get_event_loop().create_future()
        self._pending_actions[action_id] = future

        # Yuborish
        self._writer.write(message.encode())
        await self._writer.drain()

        # Javob kutish
        try:
            response = await asyncio.wait_for(future, timeout=10)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"AMI action timeout: {action}")
            self._pending_actions.pop(action_id, None)
            return None

    async def _read_events(self):
        """AMI eventlarni o'qish"""
        buffer = ""

        while self._connected:
            try:
                # Timeout bilan o'qish (60 soniya - ping har 30s yuboriladi)
                data = await asyncio.wait_for(
                    self._reader.read(4096),
                    timeout=60.0
                )

                if not data:
                    logger.error("AMI ulanish uzildi (bo'sh ma'lumot)")
                    self._connected = False
                    break

                buffer += data.decode('utf-8', errors='ignore')

                # Xabarlarni ajratish
                while "\r\n\r\n" in buffer:
                    message, buffer = buffer.split("\r\n\r\n", 1)
                    await self._handle_message(message)

            except asyncio.TimeoutError:
                # Read timeout - bu oddiy hodisa, davom etamiz
                logger.debug("AMI read timeout (60s) - davom etmoqda")
                continue
            except asyncio.CancelledError:
                logger.debug("AMI read task bekor qilindi")
                break
            except UnicodeDecodeError as e:
                logger.error(f"AMI decode xatosi: {e}")
                continue
            except Exception as e:
                logger.error(f"AMI read xatosi: {e}", exc_info=True)
                self._connected = False
                break

        self._connected = False
        logger.warning("AMI event o'qish tugadi - ulanish uzildi")

        # Ping taskni to'xtatish
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()

    async def _handle_message(self, message: str):
        """AMI xabarni qayta ishlash"""
        lines = message.strip().split("\r\n")
        data = {}

        for line in lines:
            if ": " in line:
                key, value = line.split(": ", 1)
                data[key] = value

        # Response
        if "ActionID" in data and data.get("ActionID") in self._pending_actions:
            action_id = data["ActionID"]
            future = self._pending_actions.pop(action_id)
            if not future.done():
                future.set_result(data)
            return

        # Event
        if "Event" in data:
            event_name = data["Event"]
            if event_name in self._event_handlers:
                await self._event_handlers[event_name](data)

    def on_event(self, event_name: str, handler: Callable):
        """Event handler qo'shish"""
        self._event_handlers[event_name] = handler

    def _windows_to_wsl_path(self, windows_path: str) -> str:
        """Windows pathni WSL pathga convert qilish"""
        windows_path = str(windows_path).replace("\\", "/")
        # C:/Users/... -> /mnt/c/Users/...
        if len(windows_path) > 1 and windows_path[1] == ":":
            return f"/mnt/{windows_path[0].lower()}{windows_path[2:]}"
        return windows_path

    async def originate_call(
        self,
        phone_number: str,
        audio_file: str,
        context: str = "autodialer-dynamic",
        variables: Dict[str, str] = None
    ) -> CallResult:
        """
        Qo'ng'iroq boshlash

        Args:
            phone_number: Telefon raqami
            audio_file: Audio fayl yo'li yoki nomi
            context: Asterisk context
            variables: Qo'shimcha o'zgaruvchilar

        Returns:
            CallResult
        """
        if not self._connected:
            return CallResult(status=CallStatus.FAILED, error="AMI not connected")

        # Raqamni tozalash va validatsiya
        try:
            clean_number = self._validate_and_clean_phone(phone_number)
        except ValueError as e:
            logger.error(f"Telefon validatsiya xatosi: {e}")
            return CallResult(status=CallStatus.FAILED, error=str(e))

        # Audio faylni Asterisk sounds pathga convert qilish
        # Windows pathdan faqat fayl nomini olish
        import os
        # ASTERISK_PLAYBACK_PATH: Asterisk konteyner ichidagi audio yo'l
        # ASTERISK_SOUNDS_PATH: Autodialer konteyner ichidagi audio yo'l (fayllar yoziladigan)
        default_playback = "/tmp/autodialer" if os.name == "nt" else "/var/lib/asterisk/sounds/autodialer"
        playback_path = os.getenv("ASTERISK_PLAYBACK_PATH", os.getenv("ASTERISK_SOUNDS_PATH", default_playback))
        audio_filename = Path(str(audio_file)).stem  # extension siz fayl nomi
        wsl_audio_path = f"{playback_path}/{audio_filename}"

        # Channel variable
        channel_vars = f"AUDIO_FILE={wsl_audio_path}"
        if variables:
            for k, v in variables.items():
                channel_vars += f",{k}={v}"

        logger.info(f"Qo'ng'iroq boshlanmoqda: {clean_number}, Audio: {wsl_audio_path}")

        response = await self._send_action(
            "Originate",
            Channel=f"PJSIP/{clean_number}@sarkor-endpoint",
            Context=context,
            Exten=audio_filename,  # Audio fayl nomi (hash) - dialplan uchun
            Priority="1",
            CallerID=f"WellTech <+998783331002>",
            Timeout="30000",
            Variable=channel_vars,
            Async="true"
        )

        if response and response.get("Response") == "Success":
            logger.info(f"Qo'ng'iroq yuborildi: {clean_number}")
            return CallResult(status=CallStatus.ORIGINATING)
        else:
            error = response.get("Message", "Unknown error") if response else "No response"
            logger.error(f"Qo'ng'iroq xatosi: {error}")
            return CallResult(status=CallStatus.FAILED, error=error)

    async def check_registration(self) -> bool:
        """SIP registratsiya holatini tekshirish"""
        # PJSIPQualify orqali endpoint mavjudligini tekshirish
        response = await self._send_action(
            "PJSIPQualify",
            Endpoint="sarkor-endpoint"
        )

        if response:
            resp_status = response.get("Response", "")
            logger.debug(f"SIP registration check response: {response}")
            # Success yoki endpoint mavjud bo'lsa - ishlayapti
            return resp_status == "Success"

        return False


class CallManager:
    """
    Qo'ng'iroq boshqaruvchisi

    - Qo'ng'iroqlarni rejalashtirish
    - Retry logic
    - Natijalarni kuzatish
    - PARALLEL qo'ng'iroqlar qo'llab-quvvatlanadi
    """

    def __init__(
        self,
        ami: AsteriskAMI,
        max_attempts: int = 2,
        retry_interval: int = 30
    ):
        self.ami = ami
        self.max_attempts = max_attempts
        self.retry_interval = retry_interval

        self._current_attempt = 0
        self._last_call_result: Optional[CallResult] = None
        self._call_in_progress = False
        self._call_completed_event = asyncio.Event()

        # PARALLEL qo'ng'iroqlar uchun - {phone_number: {event, result}}
        self._active_calls: Dict[str, dict] = {}

        # Event handlers
        self.ami.on_event("OriginateResponse", self._on_originate_response)
        self.ami.on_event("Hangup", self._on_hangup)
        self.ami.on_event("DialEnd", self._on_dial_end)

    async def _on_originate_response(self, data: dict):
        """Originate natijasi - qo'ng'iroq yakunlandi"""
        response = data.get("Response", "")
        reason = data.get("Reason", "")
        channel = data.get("Channel", "")

        logger.info(f"OriginateResponse: response={response}, reason={reason}, channel={channel}")

        # Natijani aniqlash: autodialer-dynamic Dial() ishlatmaydi,
        # shuning uchun DialEnd emas — OriginateResponse qo'ng'iroq natijasi
        if response == "Success":
            result = CallResult(status=CallStatus.ANSWERED, dial_status="ANSWER")
        else:
            result = CallResult(status=CallStatus.FAILED, error=reason, dial_status="NOANSWER")

        # Channel dan telefon raqamini olish: PJSIP/998xxxxxxxxx-... yoki PJSIP/998xxxxxxxxx@...
        phone_match = None
        if channel:
            match = re.search(r'PJSIP/(\d+)', channel)
            if match:
                phone_match = match.group(1)

        # Parallel qo'ng'iroq uchun signal
        if phone_match and phone_match in self._active_calls:
            call_data = self._active_calls[phone_match]
            if not call_data["event"].is_set():
                call_data["result"] = result
                call_data["event"].set()
                logger.info(f"OriginateResponse (parallel): {phone_match} -> {response}")
        elif self._active_calls:
            # Fallback: channel noma'lum bo'lsa birinchi kutayotgan callga signal
            for number, call_data in self._active_calls.items():
                if not call_data["event"].is_set():
                    call_data["result"] = result
                    call_data["event"].set()
                    logger.info(f"OriginateResponse fallback: {number} -> {response}")
                    break
        else:
            # Sequential mode
            self._last_call_result = result
            if self._call_in_progress:
                self._call_completed_event.set()

    async def _on_hangup(self, data: dict):
        """Qo'ng'iroq tugatildi"""
        channel = data.get("Channel", "")
        cause = data.get("Cause", "")
        cause_txt = data.get("Cause-txt", "")

        logger.debug(f"Hangup: channel={channel}, cause={cause} - {cause_txt}")

        # Hangup eventini e'tiborsiz qoldiramiz
        # DialEnd eventi qo'ng'iroq natijasini to'g'ri beradi
        # Hangup ko'p marta kelishi mumkin (Local channel, PJSIP channel)
        pass

    async def _on_dial_end(self, data: dict):
        """Dial tugadi - parallel qo'ng'iroqlarni qo'llab-quvvatlaydi"""
        dial_status = data.get("DialStatus", "")
        channel = data.get("Channel", "")
        dest_channel = data.get("DestChannel", "")

        logger.info(f"DialEnd keldi: channel={channel}, status={dial_status}")

        status_map = {
            "ANSWER": CallStatus.ANSWERED,
            "BUSY": CallStatus.BUSY,
            "NOANSWER": CallStatus.NO_ANSWER,
            "CANCEL": CallStatus.FAILED,
            "CONGESTION": CallStatus.FAILED,
            "CHANUNAVAIL": CallStatus.FAILED,
        }

        result = CallResult(
            status=status_map.get(dial_status, CallStatus.FAILED),
            dial_status=dial_status
        )

        # Telefon raqamini channel dan olish (PJSIP/998912345678@sarkor-endpoint)
        phone_match = None
        for ch in [dest_channel, channel]:
            if ch and "PJSIP/" in ch:
                match = re.search(r'PJSIP/(\d+)@', ch)
                if match:
                    phone_match = match.group(1)
                    break

        # PARALLEL qo'ng'iroq - tegishli call ni topish
        if phone_match and phone_match in self._active_calls:
            call_data = self._active_calls[phone_match]
            call_data["result"] = result
            call_data["event"].set()
            logger.info(f"Parallel call completed: {phone_match} -> {dial_status}")
        elif self._active_calls:
            # Channel bo'sh kelganda - faol qo'ng'iroqni topib signal qilish
            for number, call_data in self._active_calls.items():
                if not call_data["event"].is_set():
                    call_data["result"] = result
                    call_data["event"].set()
                    logger.info(f"DialEnd fallback match: {number} -> {dial_status}")
                    break
        else:
            # Eski usul - bitta qo'ng'iroq uchun
            self._last_call_result = result
            if self._call_in_progress:
                self._call_completed_event.set()

    async def make_call_with_retry(
        self,
        phone_number: str,
        audio_file: str,
        on_attempt: Callable = None,
        before_retry_check: Callable = None,
        max_attempts_override: int = None,
        retry_interval_override: int = None,
    ) -> CallResult:
        """
        Qo'ng'iroq qilish (retry bilan)

        Args:
            phone_number: Telefon raqami
            audio_file: Audio fayl
            on_attempt: Har bir urinishda chaqiriladigan callback
            before_retry_check: Retry dan oldin chaqiriladigan callback
                - (False, None) qaytarsa - to'xtatiladi
                - (True, new_audio_path) qaytarsa - yangi audio bilan davom etadi
            max_attempts_override: Per-business urinishlar soni (None = global)
            retry_interval_override: Per-business qayta qo'ng'iroq intervali (None = global)

        Returns:
            Yakuniy CallResult
        """
        effective_max = max_attempts_override if max_attempts_override is not None else self.max_attempts
        effective_retry = retry_interval_override if retry_interval_override is not None else self.retry_interval
        self._current_attempt = 0
        current_audio = audio_file

        while self._current_attempt < effective_max:
            self._current_attempt += 1

            if on_attempt:
                await on_attempt(self._current_attempt, effective_max)

            logger.info(
                f"Qo'ng'iroq urinishi {self._current_attempt}/{effective_max}: {phone_number}"
            )

            result = await self._make_single_call(phone_number, current_audio)

            if result.is_answered:
                logger.info(f"Qo'ng'iroq muvaffaqiyatli: {phone_number}")
                return result

            if self._current_attempt < effective_max:
                # Birinchi - buyurtmalarni qayta tekshirish va yangi audio olish
                if before_retry_check:
                    logger.info(f"Buyurtmalarni qayta tekshirish...")
                    check_result = await before_retry_check()

                    # Tuple (should_continue, new_audio) yoki bool qaytishi mumkin
                    if isinstance(check_result, tuple):
                        should_continue, new_audio = check_result
                        if new_audio:
                            current_audio = new_audio
                            logger.info(f"Yangi audio yangilandi: {new_audio}")
                    else:
                        should_continue = check_result

                    if not should_continue:
                        logger.info(f"Buyurtma statusi o'zgardi, qo'ng'iroq to'xtatildi: {phone_number}")
                        result.status = CallStatus.CANCELLED
                        return result

                # Keyin kutish
                logger.info(f"Qayta urinish {effective_retry}s dan keyin...")
                await asyncio.sleep(effective_retry)

        logger.warning(f"Barcha urinishlar tugadi: {phone_number}")
        return self._last_call_result or CallResult(status=CallStatus.FAILED)

    async def _make_single_call(
        self,
        phone_number: str,
        audio_file: str,
        parallel: bool = True
    ) -> CallResult:
        """Bitta qo'ng'iroq qilish - parallel qo'llab-quvvatlanadi"""

        # Telefon raqamini tozalash (faqat raqamlar)
        clean_number = re.sub(r'[^\d]', '', phone_number)
        if clean_number.startswith('998') and len(clean_number) == 12:
            pass  # OK
        elif len(clean_number) == 9:
            clean_number = '998' + clean_number

        if parallel:
            # PARALLEL rejim - har bir qo'ng'iroq mustaqil
            call_event = asyncio.Event()
            self._active_calls[clean_number] = {
                "event": call_event,
                "result": None,
                "phone": phone_number
            }

            # AMI ulanish tekshirish
            if not self.ami._connected:
                logger.warning("AMI ulanish yo'q, qayta ulanish...")
                reconnected = await self.ami.reconnect()
                if not reconnected:
                    del self._active_calls[clean_number]
                    logger.error("AMI qayta ulanish muvaffaqiyatsiz")
                    return CallResult(status=CallStatus.FAILED, error="AMI reconnect failed")

            # Qo'ng'iroq boshlash
            result = await self.ami.originate_call(phone_number, audio_file)

            if result.status == CallStatus.FAILED:
                del self._active_calls[clean_number]
                return result

            # Natija kutish
            try:
                await asyncio.wait_for(call_event.wait(), timeout=45)
            except asyncio.TimeoutError:
                logger.warning(f"Qo'ng'iroq timeout: {phone_number}")
                self._active_calls[clean_number]["result"] = CallResult(
                    status=CallStatus.NO_ANSWER,
                    error="Timeout"
                )

            # Natijani olish va tozalash
            call_result = self._active_calls.get(clean_number, {}).get("result")
            if clean_number in self._active_calls:
                del self._active_calls[clean_number]

            return call_result or CallResult(status=CallStatus.FAILED, error="No result")

        else:
            # KETMA-KET rejim (eski usul)
            if self._call_in_progress:
                logger.warning("Avvalgi qo'ng'iroq hali tugamagan, kutilmoqda...")
                try:
                    await asyncio.wait_for(self._call_completed_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    logger.error("Avvalgi qo'ng'iroq timeout, davom etilmoqda")
                await asyncio.sleep(2)

            self._call_in_progress = True
            self._call_completed_event.clear()
            self._last_call_result = None

            # AMI ulanish tekshirish va qayta ulanish
            if not self.ami._connected:
                logger.warning("AMI ulanish yo'q, qayta ulanish...")
                reconnected = await self.ami.reconnect()
                if not reconnected:
                    self._call_in_progress = False
                    logger.error("AMI qayta ulanish muvaffaqiyatsiz")
                    return CallResult(status=CallStatus.FAILED, error="AMI reconnect failed")

            # Qo'ng'iroq boshlash
            result = await self.ami.originate_call(phone_number, audio_file)

            if result.status == CallStatus.FAILED:
                self._call_in_progress = False
                return result

            # Natija kutish (30 soniya)
            try:
                await asyncio.wait_for(
                    self._call_completed_event.wait(),
                    timeout=45
                )
            except asyncio.TimeoutError:
                logger.warning("Qo'ng'iroq timeout")
                self._last_call_result = CallResult(
                    status=CallStatus.NO_ANSWER,
                    error="Timeout"
                )

            # Qo'ng'iroq tugadi
            self._call_in_progress = False
            await asyncio.sleep(2)  # 2 soniya kutish
            logger.debug("Qo'ng'iroq to'liq tugadi, davom etish mumkin")
            return self._last_call_result or CallResult(status=CallStatus.FAILED)

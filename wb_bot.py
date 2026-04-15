#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram бот для продавцов Wildberries и Ozon
Версия: 4.0 (полная поддержка двух маркетплейсов)
"""

import asyncio
import json
import os
import re
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ========== ПРОКСИ ДЛЯ ОБХОДА БЛОКИРОВОК ==========
PROXY = "http://45.155.205.233:3128"
import aiohttp
import aiosqlite

# ========== НАСТРОЙКИ ==========
TOKEN = "8548006539:AAEtYMMhyzPaXmkfZqxKYxUk3dNzJU3hlo4"
ADMIN_ID = 7976323654
PHONE_NUMBER = "+7 923 424 10 37"
PRICE = 190

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

if not os.path.exists('data'):
    os.makedirs('data')

# ========== ЮРИДИЧЕСКИЕ ТЕКСТЫ ==========

OFFER_TEXT = """
📄 *ПУБЛИЧНАЯ ОФЕРТА*

*1. ОСНОВНЫЕ ПОЛОЖЕНИЯ*
1.1. Исполнитель предоставляет Заказчику доступ к Telegram-боту для мониторинга и управления поставками на Wildberries и Ozon.
1.2. Акцептом оферты является использование бота или оплата услуг.

*2. СТОИМОСТЬ*
2.1. Стоимость услуг: 190 (Сто девяносто) рублей в месяц.
2.2. Оплата через Систему Быстрых Платежей (СБП) по номеру телефона.

*3. ОТВЕТСТВЕННОСТЬ*
3.1. ИСПОЛНИТЕЛЬ НЕ НЕСЁТ ОТВЕТСТВЕННОСТИ ЗА:
   - Любые убытки Заказчика
   - Ошибки в работе API Wildberries или Ozon
   - Сбои в работе Telegram
   - Действия третьих лиц

3.2. Бот предоставляется «КАК ЕСТЬ» (AS IS).

*4. КОНФИДЕНЦИАЛЬНОСТЬ*
4.1. Данные авторизации хранятся в зашифрованном виде.
4.2. Заказчик может удалить свои данные командой /deletedata.

*5. КОНТАКТЫ*
Email: [Ваш email]
Telegram: @[Ваш_username]

*Используя бота, вы подтверждаете согласие с условиями.*
"""

PRIVACY_TEXT = """
🔒 *ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ*

*Собираемые данные:*
• Cookies сессии Wildberries/Ozon
• ID пользователя Telegram
• Номер телефона

*Как используются:*
• Только для работы бота
• Не передаются третьим лицам

*Ваши права:*
• /deletedata — удалить все данные
• /exportdata — получить копию данных

*Используя бота, вы даёте согласие на обработку данных.*
"""

# ========== FSM СОСТОЯНИЯ ==========

class AuthStates(StatesGroup):
    waiting_for_marketplace = State()
    waiting_for_phone = State()
    waiting_for_sms = State()

class BookingStates(StatesGroup):
    waiting_for_marketplace = State()
    waiting_for_draft = State()
    waiting_for_warehouses = State()
    waiting_for_coefficient = State()
    waiting_for_dates = State()
    waiting_for_shift = State()

class TransferStates(StatesGroup):
    waiting_for_marketplace = State()
    waiting_for_sku = State()
    waiting_for_quantity = State()
    waiting_for_warehouse = State()

# ========== БАЗА ДАННЫХ ==========

class Database:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path
    
    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    cookies TEXT,
                    marketplace TEXT,
                    phone TEXT,
                    created_at TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER,
                    marketplace TEXT,
                    supply_id TEXT,
                    warehouses TEXT,
                    max_coefficient REAL,
                    dates TEXT,
                    shift_days INTEGER,
                    status TEXT,
                    result TEXT,
                    created_at TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER PRIMARY KEY,
                    active INTEGER,
                    expires_at TIMESTAMP,
                    activated_at TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS consents (
                    user_id INTEGER PRIMARY KEY,
                    agreed INTEGER,
                    agreed_at TIMESTAMP
                )
            """)
            await db.commit()
    
    async def save_session(self, user_id: int, cookies: Dict, marketplace: str, phone: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO users (user_id, cookies, marketplace, phone, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, json.dumps(cookies), marketplace, phone, datetime.now().isoformat()))
            await db.commit()
    
    async def get_session(self, user_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT cookies, marketplace FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
        if row:
            return {'cookies': json.loads(row[0]), 'marketplace': row[1]}
        return None
    
    async def delete_session(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM bookings WHERE user_id=?", (user_id,))
            await db.commit()
    
    async def save_booking(self, user_id: int, marketplace: str, data: Dict) -> str:
        booking_id = f"{marketplace}_{user_id}_{int(datetime.now().timestamp())}"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO bookings (id, user_id, marketplace, supply_id, warehouses, max_coefficient, dates, shift_days, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (booking_id, user_id, marketplace, data.get('supply_id', ''), 
                  json.dumps(data.get('warehouses', [])), data.get('max_coefficient', 2.0),
                  json.dumps(data.get('dates', [])), data.get('shift_days', 0), 'active', datetime.now().isoformat()))
            await db.commit()
        return booking_id
    
    async def get_user_bookings(self, user_id: int) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT id, marketplace, supply_id, warehouses, max_coefficient, dates, shift_days, status, result FROM bookings WHERE user_id=?", (user_id,))
            rows = await cur.fetchall()
        result = []
        for row in rows:
            result.append({
                'id': row[0], 'marketplace': row[1], 'supply_id': row[2],
                'warehouses': json.loads(row[3]) if row[3] else [],
                'max_coefficient': row[4], 'dates': json.loads(row[5]) if row[5] else [],
                'shift_days': row[6], 'status': row[7], 'result': json.loads(row[8]) if row[8] else None
            })
        return result
    
    async def get_all_active_bookings(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT id, user_id, marketplace, supply_id, warehouses, max_coefficient, dates FROM bookings WHERE status='active'")
            rows = await cur.fetchall()
        result = []
        for row in rows:
            result.append({
                'id': row[0], 'user_id': row[1], 'marketplace': row[2],
                'supply_id': row[3], 'warehouses': json.loads(row[4]) if row[4] else [],
                'max_coefficient': row[5], 'dates': json.loads(row[6]) if row[6] else []
            })
        return result
    
    async def update_booking_status(self, booking_id: str, status: str, result: Dict = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE bookings SET status=?, result=? WHERE id=?", 
                           (status, json.dumps(result) if result else None, booking_id))
            await db.commit()
    
    async def save_consent(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT OR REPLACE INTO consents (user_id, agreed, agreed_at) VALUES (?, 1, ?)",
                           (user_id, datetime.now().isoformat()))
            await db.commit()
    
    async def has_consent(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT agreed FROM consents WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
        return row and row[0] == 1
    
    async def delete_consent(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM consents WHERE user_id=?", (user_id,))
            await db.commit()
    
    async def activate_subscription(self, user_id: int, days: int = 30):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO subscriptions (user_id, active, expires_at, activated_at)
                VALUES (?, 1, ?, ?)
            """, (user_id, (datetime.now() + timedelta(days=days)).isoformat(), datetime.now().isoformat()))
            await db.commit()
    
    async def is_subscription_active(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT expires_at FROM subscriptions WHERE user_id=? AND active=1", (user_id,))
            row = await cur.fetchone()
        if row:
            return datetime.fromisoformat(row[0]) > datetime.now()
        return False

db = Database()

# ========== HTTP КЛИЕНТ ==========

class HTTPClient:
    def __init__(self):
        self._session = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
    if self._session is None or self._session.closed:
        connector = aiohttp.TCPConnector(ssl=False)
        self._session = aiohttp.ClientSession(connector=connector)
    return self._session
    
    async def request(self, method: str, url: str, cookies: Dict = None, json_data: Dict = None, params: Dict = None) -> Tuple[Optional[Dict], Optional[str]]:
        headers = headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
    'Content-Type': 'application/json'
}AppleWebKit/537.36', 'Accept': 'application/json', 'Content-Type': 'application/json'}
        try:
            session = await self._get_session()
            async with session.request(method=method, url=url, cookies=cookies or {}, json=json_data, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status}"
                try:
                    return await resp.json(), None
                except:
                    return None, "Не JSON ответ"
        except Exception as e:
            return None, str(e)

http = HTTPClient()

# ========== ФУНКЦИИ ДЛЯ WB ==========

async def wb_request_sms(phone: str) -> Tuple[bool, str, Dict]:
    phone = re.sub(r'[^0-9]', '', phone)
    if phone.startswith('8'):
        phone = '7' + phone[1:]
    if len(phone) == 10:
        phone = '7' + phone
    if len(phone) != 11:
        return False, "Неверный формат номера", {}
    result, error = await http.request('POST', 'https://www.wildberries.ru/webapi/auth/sms', json_data={"phone": phone, "isRegister": False})
    if result and result.get('errorCode') == 0:
        return True, "Код отправлен", {'phone': phone}
    return False, result.get('errorMsg', error) if result else error, {}

async def wb_verify_code(phone: str, code: str) -> Tuple[bool, str, Dict]:
    result, error = await http.request('POST', 'https://www.wildberries.ru/webapi/auth/login', json_data={"phone": phone, "code": code, "remember": True})
    if result and result.get('errorCode') == 0:
        session = await http._get_session()
        cookies = {k: v.value for k, v in session.cookie_jar.filter_cookies('https://www.wildberries.ru').items()}
        return True, "Авторизация успешна", {'cookies': cookies, 'marketplace': 'wb'}
    return False, result.get('errorMsg', error) if result else error, {}

async def wb_get_supplies(cookies: Dict) -> Optional[List[Dict]]:
    result, _ = await http.request('GET', 'https://suppliers-api.wildberries.ru/api/v3/supplies', cookies=cookies)
    if result:
        return [s for s in result if s.get('status') == 'draft']
    return None

async def wb_get_available_slots(cookies: Dict, warehouse: str = None) -> Optional[List[Dict]]:
    params = {'warehouseId': warehouse} if warehouse else {}
    result, _ = await http.request('GET', 'https://suppliers-api.wildberries.ru/api/v3/supplies/slots', cookies=cookies, params=params)
    if result:
        return [s for s in result if s.get('status') == 'available']
    return None

async def wb_book_slot(cookies: Dict, supply_id: str, slot_id: str) -> Tuple[bool, str]:
    result, error = await http.request('POST', f'https://suppliers-api.wildberries.ru/api/v3/supplies/{supply_id}/slot', cookies=cookies, json_data={"slotId": slot_id})
    if result:
        return True, "Слот забронирован"
    return False, error or "Ошибка"

async def wb_get_warehouses(cookies: Dict) -> Optional[List[Dict]]:
    result, _ = await http.request('GET', 'https://suppliers-api.wildberries.ru/api/v3/warehouses', cookies=cookies)
    return result

async def wb_transfer_stock(cookies: Dict, sku: str, warehouse_id: int, quantity: int) -> Tuple[bool, str]:
    """Перемещает товары на склад WB"""
    url = "https://suppliers-api.wildberries.ru/api/v3/stocks"
    data = [{"sku": sku, "warehouseId": warehouse_id, "quantity": quantity}]
    result, error = await http.request('PUT', url, cookies=cookies, json_data=data)
    if result:
        return True, "Перемещение выполнено успешно!"
    return False, error or "Ошибка перемещения"

# ========== ФУНКЦИИ ДЛЯ OZON ==========

async def ozon_request_sms(phone: str) -> Tuple[bool, str, Dict]:
    phone = re.sub(r'[^0-9]', '', phone)
    result, error = await http.request('POST', 'https://www.ozon.ru/api/composer-api.bx/_action/authSendCode', json_data={"phone": phone})
    if result and result.get('result'):
        return True, "Код отправлен", {'phone': phone}
    return False, result.get('error', error) if result else error, {}

async def ozon_verify_code(phone: str, code: str) -> Tuple[bool, str, Dict]:
    result, error = await http.request('POST', 'https://www.ozon.ru/api/composer-api.bx/_action/authLogin', json_data={"phone": phone, "code": code, "remember": True})
    if result and result.get('result'):
        session = await http._get_session()
        cookies = {k: v.value for k, v in session.cookie_jar.filter_cookies('https://www.ozon.ru').items()}
        return True, "Авторизация успешна", {'cookies': cookies, 'marketplace': 'ozon'}
    return False, result.get('error', error) if result else error, {}

async def ozon_get_warehouses(cookies: Dict) -> Optional[List[Dict]]:
    result, _ = await http.request('GET', 'https://www.ozon.ru/api/composer-api.bx/_action/getWarehouses', cookies=cookies)
    if result:
        return result.get('warehouses', [])
    return None

# ========== КЛАВИАТУРЫ ==========

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟣 WB Авторизация"), KeyboardButton(text="🟢 Ozon Авторизация")],
        [KeyboardButton(text="🎯 Автобронирование"), KeyboardButton(text="📋 Мои заявки")],
        [KeyboardButton(text="🚚 Переместить товары")],
        [KeyboardButton(text="💳 Оплатить"), KeyboardButton(text="📜 Оферта")],
        [KeyboardButton(text="❌ Выйти"), KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True
)

marketplace_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🟣 Wildberries", callback_data="mp_wb")],
    [InlineKeyboardButton(text="🟢 Ozon", callback_data="mp_ozon")],
    [InlineKeyboardButton(text="❌ Отмена", callback_data="mp_cancel")]
])

# ========== ОБРАБОТЧИКИ ==========

@dp.message(Command("start"))
async def start(message: types.Message):
    if not await db.has_consent(message.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Принимаю", callback_data="accept_terms")],
            [InlineKeyboardButton(text="❌ Не принимаю", callback_data="decline_terms")]
        ])
        await message.answer(
            "🔐 *ПЕРЕД НАЧАЛОМ РАБОТЫ*\n\n"
            "Ознакомьтесь с:\n• /offer — Публичная оферта\n• /privacy — Политика конфиденциальности\n\n"
            "Вы принимаете условия?",
            parse_mode="Markdown", reply_markup=kb
        )
        return
    await message.answer(
        "🤖 *МАРКЕТПЛЕЙС БОТ (WB + OZON)*\n\n"
        "Выберите маркетплейс для авторизации:",
        parse_mode="Markdown", reply_markup=main_keyboard
    )

@dp.callback_query(lambda c: c.data == "accept_terms")
async def accept_terms(callback: types.CallbackQuery):
    await db.save_consent(callback.from_user.id)
    await callback.message.edit_text("✅ Спасибо! Теперь вы можете использовать бота. Нажмите /start")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "decline_terms")
async def decline_terms(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Без согласия с условиями использование бота невозможно.")
    await callback.answer()

@dp.message(lambda msg: msg.text == "🟣 WB Авторизация")
async def auth_wb(message: types.Message, state: FSMContext):
    await state.update_data(marketplace='wb')
    await message.answer("🔐 *Введите номер телефона от кабинета WB:*\nФорматы: +7XXXXXXXXXX, 8XXXXXXXXXX", parse_mode="Markdown")
    await state.set_state(AuthStates.waiting_for_phone)

@dp.message(lambda msg: msg.text == "🟢 Ozon Авторизация")
async def auth_ozon(message: types.Message, state: FSMContext):
    await state.update_data(marketplace='ozon')
    await message.answer("🔐 *Введите номер телефона от кабинета Ozon:*", parse_mode="Markdown")
    await state.set_state(AuthStates.waiting_for_phone)

@dp.message(AuthStates.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    data = await state.get_data()
    marketplace = data.get('marketplace')
    phone = message.text.strip()
    
    if marketplace == 'wb':
        success, msg, auth_data = await wb_request_sms(phone)
    else:
        success, msg, auth_data = await ozon_request_sms(phone)
    
    if success:
        await state.update_data(phone=auth_data.get('phone'))
        await message.answer(f"✅ {msg}\n\nВведите СМС-код:")
        await state.set_state(AuthStates.waiting_for_sms)
    else:
        await message.answer(f"❌ {msg}\nПопробуйте снова.")

@dp.message(AuthStates.waiting_for_sms)
async def process_sms(message: types.Message, state: FSMContext):
    data = await state.get_data()
    marketplace = data.get('marketplace')
    phone = data.get('phone')
    code = message.text.strip()
    
    if marketplace == 'wb':
        success, msg, auth_data = await wb_verify_code(phone, code)
    else:
        success, msg, auth_data = await ozon_verify_code(phone, code)
    
    if success:
        await db.save_session(message.from_user.id, auth_data['cookies'], marketplace, phone)
        await message.answer(f"✅ {msg}\n\nТеперь вы можете создавать заявки на автобронирование!", parse_mode="Markdown")
        await state.clear()
    else:
        await message.answer(f"❌ {msg}\nПопробуйте снова.")

@dp.message(lambda msg: msg.text == "🎯 Автобронирование")
async def create_booking(message: types.Message, state: FSMContext):
    session = await db.get_session(message.from_user.id)
    if not session:
        await message.answer("⚠️ Сначала авторизуйтесь!")
        return
    
    await message.answer("Выберите маркетплейс:", reply_markup=marketplace_kb)
    await state.set_state(BookingStates.waiting_for_marketplace)

@dp.callback_query(lambda c: c.data.startswith("mp_"))
async def select_marketplace(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "mp_cancel":
        await callback.message.edit_text("❌ Отменено")
        await state.clear()
        await callback.answer()
        return
    
    marketplace = callback.data.split("_")[1]
    session = await db.get_session(callback.from_user.id)
    
    if not session or session.get('marketplace') != marketplace:
        await callback.message.edit_text(f"⚠️ Сначала авторизуйтесь в {marketplace.upper()} через кнопку авторизации")
        await callback.answer()
        return
    
    cookies = session.get('cookies')
    
    if marketplace == 'wb':
        supplies = await wb_get_supplies(cookies)
    else:
        await callback.message.edit_text("🚧 Функция для Ozon в разработке")
        await callback.answer()
        return
    
    if not supplies:
        await callback.message.edit_text("⚠️ Нет черновиков поставок. Создайте их в кабинете.")
        await callback.answer()
        return
    
    await state.update_data(marketplace=marketplace, supplies=supplies)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for supply in supplies[:10]:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📄 {supply.get('name', 'Без названия')[:40]}", callback_data=f"draft_{supply.get('id')}")])
    
    await callback.message.edit_text("📄 Выберите черновик поставки:", reply_markup=kb)
    await state.set_state(BookingStates.waiting_for_draft)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("draft_"))
async def select_draft(callback: types.CallbackQuery, state: FSMContext):
    supply_id = callback.data.split("_")[1]
    data = await state.get_data()
    supplies = data.get('supplies', [])
    selected = next((s for s in supplies if s.get('id') == supply_id), None)
    
    if not selected:
        await callback.message.edit_text("❌ Черновик не найден")
        await callback.answer()
        return
    
    await state.update_data(supply_id=supply_id, supply_name=selected.get('name'))
    await callback.message.edit_text("🏢 Введите склады через запятую (до 5):\nПример: `Электросталь, Коледино, Подольск`", parse_mode="Markdown")
    await state.set_state(BookingStates.waiting_for_warehouses)
    await callback.answer()

@dp.message(BookingStates.waiting_for_warehouses)
async def set_warehouses(message: types.Message, state: FSMContext):
    warehouses = [w.strip() for w in message.text.split(',')]
    if len(warehouses) > 5:
        await message.answer("❌ Не более 5 складов")
        return
    await state.update_data(warehouses=warehouses)
    await message.answer("📊 Введите максимальный коэффициент (например: `1.5`, `2.0`):\n💡 Если не уверены — `2.0`", parse_mode="Markdown")
    await state.set_state(BookingStates.waiting_for_coefficient)

@dp.message(BookingStates.waiting_for_coefficient)
async def set_coefficient(message: types.Message, state: FSMContext):
    try:
        coef = float(message.text.replace(',', '.'))
        if coef < 0.1 or coef > 10:
            raise ValueError
        await state.update_data(max_coefficient=coef)
    except:
        await message.answer("❌ Введите число от 0.1 до 10")
        return
    await message.answer("📅 Введите даты (ГГГГ-ММ-ДД):\nПример: `2025-10-15, 2025-10-20`", parse_mode="Markdown")
    await state.set_state(BookingStates.waiting_for_dates)

@dp.message(BookingStates.waiting_for_dates)
async def set_dates(message: types.Message, state: FSMContext):
    dates = [d.strip() for d in re.split(r'[ ,]+', message.text) if re.match(r'^\d{4}-\d{2}-\d{2}$', d.strip())]
    if not dates:
        await message.answer("❌ Неверный формат дат")
        return
    await state.update_data(dates=dates)
    await message.answer("⏱ Введите сдвиг поиска (дни):\n0 — искать с сегодня\n3 — минимум на 3 дня вперёд\n\nЕсли не уверены — `0`", parse_mode="Markdown")
    await state.set_state(BookingStates.waiting_for_shift)

@dp.message(BookingStates.waiting_for_shift)
async def set_shift(message: types.Message, state: FSMContext):
    try:
        shift = int(message.text)
        if shift < 0 or shift > 10:
            raise ValueError
    except:
        await message.answer("❌ Введите число от 0 до 10")
        return
    
    data = await state.get_data()
    booking_data = {
        'supply_id': data.get('supply_id'),
        'warehouses': data.get('warehouses'),
        'max_coefficient': data.get('max_coefficient'),
        'dates': data.get('dates'),
        'shift_days': shift
    }
    
    booking_id = await db.save_booking(message.from_user.id, data.get('marketplace'), booking_data)
    
    await message.answer(
        f"✅ *ЗАЯВКА СОЗДАНА!*\n\n"
        f"🆔 ID: `{booking_id}`\n"
        f"📄 Поставка: {data.get('supply_name', 'Неизвестно')}\n"
        f"🏢 Склады: {', '.join(data.get('warehouses', []))}\n"
        f"📊 Коэфф: ≤ {data.get('max_coefficient')}\n"
        f"📅 Даты: {', '.join(data.get('dates', []))}\n"
        f"⏱ Сдвиг: {shift} дней\n\n"
        f"🔍 Бот начал мониторинг!",
        parse_mode="Markdown"
    )
    await state.clear()

@dp.message(lambda msg: msg.text == "📋 Мои заявки")
async def show_bookings(message: types.Message):
    bookings = await db.get_user_bookings(message.from_user.id)
    if not bookings:
        await message.answer("📭 У вас нет заявок")
        return
    text = "📋 *ВАШИ ЗАЯВКИ*\n\n"
    for b in bookings:
        mp_emoji = "🟣" if b['marketplace'] == 'wb' else "🟢"
        status_emoji = "🟢" if b['status'] == 'active' else "✅" if b['status'] == 'completed' else "❌"
        text += f"{mp_emoji} {status_emoji} `{b['id']}`\n"
        text += f"   Коэфф: ≤ {b['max_coefficient']}\n"
        if b['result']:
            text += f"   ✅ Забронирован: {b['result'].get('slot_date', '')}\n"
        text += "\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "🚚 Переместить товары")
async def transfer_start(message: types.Message, state: FSMContext):
    session = await db.get_session(message.from_user.id)
    if not session:
        await message.answer("⚠️ Сначала авторизуйтесь!")
        return
    await message.answer("Выберите маркетплейс:", reply_markup=marketplace_kb)
    await state.set_state(TransferStates.waiting_for_marketplace)

@dp.callback_query(lambda c: c.data.startswith("transfer_mp_"))
async def transfer_marketplace(callback: types.CallbackQuery, state: FSMContext):
    marketplace = callback.data.split("_")[2]
    session = await db.get_session(callback.from_user.id)
    
    if not session or session.get('marketplace') != marketplace:
        await callback.message.edit_text(f"⚠️ Сначала авторизуйтесь в {marketplace.upper()}")
        await callback.answer()
        return
    
    await state.update_data(marketplace=marketplace)
    await callback.message.edit_text("🚚 Введите SKU товара:")
    await state.set_state(TransferStates.waiting_for_sku)
    await callback.answer()

@dp.message(TransferStates.waiting_for_sku)
async def transfer_sku(message: types.Message, state: FSMContext):
    await state.update_data(sku=message.text.strip())
    await message.answer("📦 Введите количество:")
    await state.set_state(TransferStates.waiting_for_quantity)

@dp.message(TransferStates.waiting_for_quantity)
async def transfer_quantity(message: types.Message, state: FSMContext):
    try:
        qty = int(message.text)
        if qty <= 0:
            raise ValueError
        await state.update_data(quantity=qty)
    except:
        await message.answer("❌ Введите положительное число")
        return
    
    data = await state.get_data()
    session = await db.get_session(message.from_user.id)
    
    if data.get('marketplace') == 'wb':
        warehouses = await wb_get_warehouses(session['cookies'])
    else:
        warehouses = await ozon_get_warehouses(session['cookies'])
    
    if not warehouses:
        await message.answer("❌ Не удалось загрузить склады")
        await state.clear()
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for wh in warehouses[:8]:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📍 {wh.get('name', 'Склад')}", callback_data=f"transfer_wh_{wh.get('id')}")])
    
    await state.update_data(warehouses=warehouses)
    await message.answer("🏢 Выберите склад:", reply_markup=kb)
    await state.set_state(TransferStates.waiting_for_warehouse)

@dp.callback_query(lambda c: c.data.startswith("transfer_wh_"))
async def transfer_execute(callback: types.CallbackQuery, state: FSMContext):
    warehouse_id = callback.data.split("_")[2]
    data = await state.get_data()
    session = await db.get_session(callback.from_user.id)
    
    await callback.message.edit_text(f"🚚 Перемещаю {data.get('quantity')} шт...")
    
    if data.get('marketplace') == 'wb':
        result, msg = await wb_transfer_stock(session['cookies'], data.get('sku'), int(warehouse_id), data.get('quantity'))
    else:
        result, msg = False, "Функция для Ozon в разработке"
    
    if result:
        await callback.message.edit_text(f"✅ Перемещено {data.get('quantity')} шт. товара {data.get('sku')}")
    else:
        await callback.message.edit_text(f"❌ {msg}")
    
    await state.clear()
    await callback.answer()

@dp.message(lambda msg: msg.text == "💳 Оплатить")
async def payment(message: types.Message):
    if await db.is_subscription_active(message.from_user.id):
        await message.answer("✅ *Подписка активна!*", parse_mode="Markdown")
        return
    
    text = f"""
💳 *ОПЛАТА ПОДПИСКИ*

💰 Стоимость: {PRICE} ₽/месяц

📱 *Как оплатить через СБП:*
1. Откройте приложение банка
2. Выберите «СБП» → «Оплата по номеру телефона»
3. Введите номер: `{PHONE_NUMBER}`
4. Сумма: `{PRICE}` ₽
5. В комментарии: `{message.from_user.id}`

✅ После оплаты нажмите «Я оплатил»
"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data="payment_done")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="payment_help")]
    ])
    await message.answer(text, parse_mode="Markdown", reply_markup=kb)

@dp.callback_query(lambda c: c.data == "payment_done")
async def payment_done(callback: types.CallbackQuery):
    await callback.message.edit_text("🔍 Запрос отправлен администратору. Ожидайте подтверждения...")
    await bot.send_message(ADMIN_ID, f"💰 ОПЛАТА\nПользователь: @{callback.from_user.username}\nID: {callback.from_user.id}\nСумма: {PRICE} ₽\n/activate_{callback.from_user.id} — активировать")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "payment_help")
async def payment_help(callback: types.CallbackQuery):
    await callback.message.edit_text("❓ Если оплата не проходит, напишите администратору @support")
    await callback.answer()

@dp.message(lambda msg: str(msg.from_user.id) == str(ADMIN_ID) and msg.text.startswith("/activate_"))
async def activate_user(message: types.Message):
    user_id = int(message.text.split("_")[1])
    await db.activate_subscription(user_id)
    await message.answer(f"✅ Подписка активирована для {user_id}")
    await bot.send_message(user_id, "🎉 *ПОДПИСКА АКТИВИРОВАНА!* Спасибо!", parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "📜 Оферта")
async def show_offer(message: types.Message):
    await message.answer(OFFER_TEXT, parse_mode="Markdown")

@dp.message(Command("privacy"))
async def show_privacy(message: types.Message):
    await message.answer(PRIVACY_TEXT, parse_mode="Markdown")

@dp.message(Command("deletedata"))
async def delete_data(message: types.Message):
    await db.delete_session(message.from_user.id)
    await db.delete_consent(message.from_user.id)
    await message.answer("🗑 *Все ваши данные удалены*", parse_mode="Markdown")

@dp.message(Command("exportdata"))
async def export_data(message: types.Message):
    session = await db.get_session(message.from_user.id)
    export = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "has_session": session is not None,
        "marketplace": session.get('marketplace') if session else None,
        "export_date": datetime.now().isoformat()
    }
    await message.answer(f"📄 *ЭКСПОРТ ДАННЫХ*\n\n```json\n{json.dumps(export, indent=2, ensure_ascii=False)}\n```", parse_mode="Markdown")

@dp.message(lambda msg: msg.text == "❌ Выйти")
async def logout(message: types.Message):
    await db.delete_session(message.from_user.id)
    await message.answer("✅ Вы вышли из аккаунта")

@dp.message(lambda msg: msg.text == "ℹ️ Помощь")
async def help_command(message: types.Message):
    await message.answer("""
ℹ️ *ПОМОЩЬ*

🔐 *Авторизация* — вход через номер телефона
🎯 *Автобронирование* — создание заявки на поиск слотов
📋 *Мои заявки* — просмотр активных заявок
🚚 *Переместить товары* — перенос товаров между складами
💳 *Оплатить* — продление подписки

📜 *Документы:* /offer, /privacy
    """, parse_mode="Markdown")

# ========== ФОНОВЫЙ МОНИТОРИНГ ==========

async def monitor_loop():
    while True:
        try:
            bookings = await db.get_all_active_bookings()
            for booking in bookings:
                session = await db.get_session(booking['user_id'])
                if not session:
                    continue
                for warehouse in booking['warehouses']:
                    if booking['marketplace'] == 'wb':
                        slots = await wb_get_available_slots(session['cookies'], warehouse)
                        if slots:
                            for slot in slots:
                                if slot.get('coefficient', 0) <= booking['max_coefficient'] and slot.get('date', '') in booking['dates']:
                                    success, _ = await wb_book_slot(session['cookies'], booking['supply_id'], slot.get('id'))
                                    if success:
                                        await bot.send_message(booking['user_id'], f"🎉 *СЛОТ ЗАБРОНИРОВАН!*\n📅 {slot.get('date')}\n📊 Коэфф: {slot.get('coefficient')}\n🏢 {warehouse}", parse_mode="Markdown")
                                        await db.update_booking_status(booking['id'], 'completed', {'slot_date': slot.get('date'), 'warehouse': warehouse})
                                        break
            await asyncio.sleep(30)
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            await asyncio.sleep(60)

# ========== ЗАПУСК ==========

async def main():
    await db.init()
    asyncio.create_task(monitor_loop())
    print("=" * 50)
    print("🤖 МАРКЕТПЛЕЙС БОТ (WB + OZON)")
    print("=" * 50)
    print("✅ Бот запущен!")
    print("=" * 50)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

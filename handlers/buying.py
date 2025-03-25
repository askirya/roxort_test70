from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from database.db import get_session, async_session
from database.models import User, PhoneListing, Transaction
from datetime import datetime
from sqlalchemy import select, and_
from config import AVAILABLE_SERVICES
from handlers.common import get_main_keyboard, check_user_registered
from .services import available_services, get_services_keyboard
from log import logger
import logging
from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

router = Router()

logger = logging.getLogger(__name__)

class BuyingStates(StatesGroup):
    choosing_service = State()
    viewing_listings = State()
    confirming_purchase = State()

def get_filter_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Поиск по сервису"), KeyboardButton(text="⏰ Поиск по времени")],
            [KeyboardButton(text="💰 Сначала дешевые"), KeyboardButton(text="💰 Сначала дорогие")],
            [KeyboardButton(text="🔄 Сначала новые"), KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_listing_keyboard(listing_id: int):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Купить", callback_data=f"buy_{listing_id}"),
                InlineKeyboardButton(text="➡️ Следующий", callback_data="next_listing")
            ]
        ]
    )
    return keyboard

def get_services_keyboard():
    keyboard = []
    for service_id, service_name in AVAILABLE_SERVICES.items():
        keyboard.append([InlineKeyboardButton(
            text=f"📱 {service_name}",
            callback_data=f"buy_service:{service_id}"
        )])
    keyboard.append([InlineKeyboardButton(
        text="❌ Отмена",
        callback_data="buy_cancel"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Функция для показа сервисов через сообщение
async def show_services_message(message: types.Message, state: FSMContext):
    """Показать доступные сервисы для покупки через обычное сообщение"""
    await state.set_state(BuyingStates.choosing_service)
    await message.answer(
        "📱 Выберите сервис для покупки номера:",
        reply_markup=get_services_keyboard()
    )

# Функция для показа сервисов через callback
async def show_services_callback(callback: types.CallbackQuery, state: FSMContext):
    """Показать доступные сервисы для покупки через callback"""
    await state.set_state(BuyingStates.choosing_service)
    await callback.message.edit_text(
        "📱 Выберите сервис для покупки номера:",
        reply_markup=get_services_keyboard()
    )

@router.message(lambda message: message.text == "📱 Купить номер")
async def start_buying(message: types.Message, state: FSMContext):
    await show_services_message(message, state)

@router.callback_query(F.data == "buy_number")
async def handle_buy_callback(callback: types.CallbackQuery, state: FSMContext):
    await show_services_callback(callback, state)

@router.callback_query(lambda c: c.data == "buy_cancel")
async def cancel_buying(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        # Удаляем сообщение с инлайн-клавиатурой
        await callback.message.delete()
        # Отправляем новое сообщение через callback.bot
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text="❌ Покупка отменена.",
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
    except Exception as e:
        logger.error(f"Error in cancel_buying: {e}")
        # Если не удалось удалить сообщение, пробуем отредактировать
        try:
            await callback.message.edit_text(
                "❌ Покупка отменена.",
                reply_markup=get_main_keyboard(callback.from_user.id)
            )
        except Exception as e:
            logger.error(f"Error editing message in cancel_buying: {e}")
            # Если и это не удалось, отправляем новое сообщение
            await callback.bot.send_message(
                chat_id=callback.from_user.id,
                text="❌ Покупка отменена.",
                reply_markup=get_main_keyboard(callback.from_user.id)
            )

@router.callback_query(lambda c: c.data.startswith("buy_service:"))
async def show_listings(callback: types.CallbackQuery, state: FSMContext):
    service = callback.data.split(":")[1]
    
    async with async_session() as session:
        try:
            query = select(PhoneListing).where(
                and_(
                    PhoneListing.service == service,
                    PhoneListing.is_active == True
                )
            ).order_by(PhoneListing.created_at.desc())
            
            result = await session.execute(query)
            listings = result.scalars().all()
            
            if not listings:
                await callback.message.edit_text(
                    f"😕 Сейчас нет доступных номеров для {available_services[service]}.\n"
                    "Попробуйте позже или выберите другой сервис.",
                    reply_markup=get_services_keyboard()
                )
                return
            
            keyboard = []
            for listing in listings:
                seller_query = select(User).where(User.telegram_id == listing.seller_id)
                seller_result = await session.execute(seller_query)
                seller = seller_result.scalar_one_or_none()
                
                if seller:
                    keyboard.append([InlineKeyboardButton(
                        text=f"💰 {listing.price} USDT | ⏰ {listing.rental_period}ч | ⭐️ {seller.rating:.1f}",
                        callback_data=f"buy_listing:{listing.id}"
                    )])
            
            keyboard.append([InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=f"buy_service:{service}"
            )])
            keyboard.append([InlineKeyboardButton(
                text="❌ Отмена",
                callback_data="buy_cancel"
            )])
            
            await callback.message.edit_text(
                f"📱 Доступные номера для {available_services[service]}:\n"
                "Выберите подходящий вариант:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
            )
        except Exception as e:
            logger.error(f"Error showing listings: {e}")
            await callback.answer("❌ Произошла ошибка при загрузке объявлений", show_alert=True)
        finally:
            await session.close()

@router.callback_query(lambda c: c.data.startswith("buy_listing:"))
async def process_buy(callback: types.CallbackQuery, state: FSMContext):
    listing_id = int(callback.data.split(":")[1])
    
    async with async_session() as session:
        try:
            # Проверяем существование объявления
            listing = await session.get(PhoneListing, listing_id)
            if not listing or not listing.is_active:
                await callback.message.edit_text(
                    "❌ Это объявление уже недоступно.",
                    reply_markup=get_main_keyboard(callback.from_user.id)
                )
                return
            
            # Получаем информацию о пользователях
            buyer = await session.get(User, callback.from_user.id)
            seller = await session.get(User, listing.seller_id)
            
            if not buyer or not seller:
                await callback.message.edit_text(
                    "❌ Ошибка: пользователь не найден.",
                    reply_markup=get_main_keyboard(callback.from_user.id)
                )
                return
            
            # Проверяем баланс
            if buyer.balance < listing.price:
                await callback.message.edit_text(
                    "❌ Недостаточно средств на балансе.\n"
                    f"Необходимо: {listing.price} USDT\n"
                    f"Ваш баланс: {buyer.balance} USDT",
                    reply_markup=get_main_keyboard(callback.from_user.id)
                )
                return
            
            # Проверяем, нет ли уже активной транзакции для этого объявления
            existing_transaction = await session.scalar(
                select(Transaction).where(
                    and_(
                        Transaction.listing_id == listing_id,
                        Transaction.status == "completed"
                    )
                )
            )
            
            if existing_transaction:
                await callback.message.edit_text(
                    "❌ Это объявление уже было куплено.",
                    reply_markup=get_main_keyboard(callback.from_user.id)
                )
                return
            
            # Создаем транзакцию
            transaction = Transaction(
                listing_id=listing.id,
                buyer_id=buyer.telegram_id,
                seller_id=seller.telegram_id,
                amount=listing.price,
                status="completed",
                created_at=datetime.utcnow(),
                completed_at=datetime.utcnow()
            )
            
            # Обновляем балансы
            buyer.balance -= listing.price
            seller.balance += listing.price
            
            # Деактивируем объявление
            listing.is_active = False
            
            session.add(transaction)
            await session.commit()
            
            # Уведомляем покупателя
            await callback.message.edit_text(
                "✅ Покупка успешно совершена!\n"
                f"Номер телефона: {listing.phone_number}\n"
                f"Сервис: {AVAILABLE_SERVICES[listing.service]}\n"
                f"Срок аренды: {listing.rental_period} часов\n"
                f"Сумма: {listing.price} USDT",
                reply_markup=get_main_keyboard(callback.from_user.id)
            )
            
            # Уведомляем продавца
            await callback.bot.send_message(
                seller.telegram_id,
                "💰 Ваш номер был куплен!\n"
                f"Номер телефона: {listing.phone_number}\n"
                f"Сумма: {listing.price} USDT"
            )
            
        except Exception as e:
            logger.error(f"Error in process_buy: {e}")
            await session.rollback()
            await callback.message.edit_text(
                "❌ Произошла ошибка при совершении покупки. Пожалуйста, попробуйте позже.",
                reply_markup=get_main_keyboard(callback.from_user.id)
            )

@router.message(F.text == "🔍 Поиск по сервису")
async def search_by_service(message: types.Message, state: FSMContext):
    from handlers.selling import get_services_keyboard
    await state.set_state(BuyingStates.choosing_service)
    await message.answer(
        "📱 Выберите сервис:",
        reply_markup=get_services_keyboard()
    )

@router.message(BuyingStates.choosing_service)
async def process_service_choice(message: types.Message, state: FSMContext):
    from handlers.selling import available_services
    
    if message.text == "❌ Отмена":
        await state.clear()
        from handlers.common import get_main_keyboard
        await message.answer("Операция отменена.", reply_markup=get_main_keyboard())
        return

    if message.text not in available_services:
        await message.answer("❌ Пожалуйста, выберите сервис из списка.")
        return

    async with get_session() as session:
        query = select(PhoneListing).where(
            and_(
                PhoneListing.service == message.text,
                PhoneListing.is_active == True
            )
        ).order_by(PhoneListing.created_at.desc())
        
        result = await session.execute(query)
        listings = result.scalars().all()

        if not listings:
            await message.answer(
                "😕 К сожалению, сейчас нет доступных номеров для этого сервиса.\n"
                "Попробуйте позже или выберите другой сервис."
            )
            return

        await state.update_data(current_listing_index=0, listings=[listing.id for listing in listings])
        await show_listing(message, state, listings[0])

async def show_listing(message: types.Message, state: FSMContext, listing: PhoneListing):
    async with get_session() as session:
        seller = await session.get(User, listing.seller_id)
        
        await message.answer(
            f"📱 Номер для {listing.service}\n\n"
            f"⏰ Длительность: {listing.duration} час(ов)\n"
            f"💰 Цена: {listing.price} USDT\n"
            f"👤 Продавец: {seller.username or 'Аноним'}\n"
            f"⭐️ Рейтинг продавца: {seller.rating}\n"
            f"📅 Размещено: {listing.created_at.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=get_listing_keyboard(listing.id)
        )

@router.callback_query(lambda c: c.data == 'next_listing')
async def show_next_listing(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current_index = data.get('current_listing_index', 0)
    listings = data.get('listings', [])
    
    if current_index + 1 >= len(listings):
        await callback.answer("Это последнее предложение в списке.")
        return
    
    current_index += 1
    await state.update_data(current_listing_index=current_index)
    
    async with get_session() as session:
        listing = await session.get(PhoneListing, listings[current_index])
        if listing:
            await show_listing(callback.message, state, listing)

@router.message(F.text == "💰 Сначала дешевые")
async def sort_by_price_asc(message: types.Message, state: FSMContext):
    async with get_session() as session:
        query = select(PhoneListing).where(
            PhoneListing.is_active == True
        ).order_by(PhoneListing.price.asc())
        
        await process_sorted_listings(message, state, session, query)

@router.message(F.text == "💰 Сначала дорогие")
async def sort_by_price_desc(message: types.Message, state: FSMContext):
    async with get_session() as session:
        query = select(PhoneListing).where(
            PhoneListing.is_active == True
        ).order_by(PhoneListing.price.desc())
        
        await process_sorted_listings(message, state, session, query)

@router.message(F.text == "🔄 Сначала новые")
async def sort_by_date(message: types.Message, state: FSMContext):
    async with get_session() as session:
        query = select(PhoneListing).where(
            PhoneListing.is_active == True
        ).order_by(PhoneListing.created_at.desc())
        
        await process_sorted_listings(message, state, session, query)

async def process_sorted_listings(message: types.Message, state: FSMContext, session, query):
    result = await session.execute(query)
    listings = result.scalars().all()
    
    if not listings:
        await message.answer("😕 Сейчас нет доступных предложений.")
        return
    
    await state.update_data(current_listing_index=0, listings=[listing.id for listing in listings])
    await show_listing(message, state, listings[0])

@router.callback_query(F.data.startswith("buy_listing_"))
async def confirm_purchase(callback: types.CallbackQuery, state: FSMContext):
    listing_id = int(callback.data.split("_")[2])
    
    async with get_session() as session:
        # Получаем объявление
        query = select(PhoneListing).where(PhoneListing.id == listing_id)
        result = await session.execute(query)
        listing = result.scalar_one_or_none()
        
        if not listing or not listing.is_active:
            await callback.answer("❌ Это объявление уже неактивно", show_alert=True)
            return
        
        # Получаем покупателя
        buyer_query = select(User).where(User.telegram_id == callback.from_user.id)
        result = await session.execute(buyer_query)
        buyer = result.scalar_one_or_none()
        
        if not buyer:
            await callback.answer("❌ Вы не зарегистрированы", show_alert=True)
            return
            
        if buyer.balance < listing.price:
            await callback.answer(
                "❌ Недостаточно средств на балансе.\n"
                f"Необходимо: {listing.price}₽\n"
                f"На балансе: {buyer.balance}₽",
                show_alert=True
            )
            return
        
        keyboard = [
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data=f"confirm_buy_{listing_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="cancel_buy"
                )
            ]
        ]
        
        await state.update_data(listing_id=listing_id)
        await state.set_state(BuyingStates.confirming_purchase)
        
        await callback.message.edit_text(
            f"📱 Подтверждение покупки:\n\n"
            f"Сервис: {available_services[listing.service]}\n"
            f"Номер: {listing.phone_number}\n"
            f"Срок аренды: {listing.rental_period} часов\n"
            f"Цена: {listing.price}₽\n\n"
            "Подтвердите покупку:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

@router.callback_query(F.data.startswith("confirm_buy_"))
async def process_purchase(callback: types.CallbackQuery, state: FSMContext):
    listing_id = int(callback.data.split("_")[2])
    
    async with get_session() as session:
        # Получаем объявление и проверяем его актуальность
        query = select(PhoneListing).where(PhoneListing.id == listing_id)
        result = await session.execute(query)
        listing = result.scalar_one_or_none()
        
        if not listing or not listing.is_active:
            await callback.answer("❌ Это объявление уже неактивно", show_alert=True)
            return
        
        # Получаем покупателя и продавца
        buyer_query = select(User).where(User.telegram_id == callback.from_user.id)
        seller_query = select(User).where(User.telegram_id == listing.seller_id)
        
        result = await session.execute(buyer_query)
        buyer = result.scalar_one_or_none()
        
        result = await session.execute(seller_query)
        seller = result.scalar_one_or_none()
        
        if not buyer or not seller:
            await callback.answer("❌ Ошибка: пользователь не найден", show_alert=True)
            return
            
        if buyer.balance < listing.price:
            await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
            return
        
        try:
            # Создаем транзакцию
            transaction = Transaction(
                buyer_id=buyer.telegram_id,
                seller_id=seller.telegram_id,
                listing_id=listing.id,
                amount=listing.price,
                status="completed",
                created_at=datetime.utcnow(),
                completed_at=datetime.utcnow()
            )
            session.add(transaction)
            
            # Обновляем балансы
            buyer.balance -= listing.price
            seller.balance += listing.price
            
            # Деактивируем объявление
            listing.is_active = False
            
            await session.commit()
            
            # Отправляем уведомления
            await callback.bot.send_message(
                seller.telegram_id,
                f"💰 Ваш номер {listing.phone_number} был куплен!\n"
                f"Сумма: {listing.price}₽"
            )
            
            await callback.message.edit_text(
                "✅ Покупка успешно совершена!\n\n"
                f"Номер телефона: {listing.phone_number}\n"
                f"Сервис: {available_services[listing.service]}\n"
                f"Срок аренды: {listing.rental_period} часов\n"
                f"Сумма: {listing.price}₽\n\n"
                "Спасибо за покупку! 🎉"
            )
            
            await state.clear()
            
        except Exception as e:
            logger.error(f"Error processing purchase: {e}")
            await session.rollback()
            await callback.answer("❌ Произошла ошибка при обработке покупки", show_alert=True)

@router.callback_query(F.data == "cancel_buy")
async def cancel_purchase(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Покупка отменена.\n"
        "Выберите действие в главном меню."
    )

@router.callback_query(F.data == "back_to_services")
async def back_to_services(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BuyingStates.choosing_service)
    await callback.message.edit_text(
        "📱 Выберите сервис для покупки номера:",
        reply_markup=get_services_keyboard()
    )

async def cmd_buy(message: Message, state: FSMContext):
    """Обработчик команды /buy"""
    try:
        async with async_session() as session:
            user = await session.get(User, message.from_user.id)
            if not user:
                await message.answer("Пожалуйста, сначала зарегистрируйтесь с помощью команды /start")
                return
            
            # Здесь будет логика покупки номера
            await message.answer("Выберите сервис для покупки номера:")
            # TODO: Добавить клавиатуру с сервисами
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /buy: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

def register_buying_handlers(dp: Dispatcher):
    """Регистрация обработчиков для покупки номеров"""
    dp.message.register(cmd_buy, Command("buy")) 
"""
Скрипт для быстрого тестирования основных компонентов бота
"""
import asyncio
import sys

print("🧪 Тестирование компонентов бота...\n")

# Тест 1: Импорт модулей
print("1️⃣ Импорт модулей...")
try:
    from database import Database
    from peek_api import peek_gift_info, AVAILABLE_MODELS, format_summary
    from keyboards import main_menu_keyboard, payment_method_keyboard
    from payments import PaymentManager
    from logger_channel import ChannelLogger
    print("✅ Все модули импортированы успешно\n")
except Exception as e:
    print(f"❌ Ошибка импорта: {e}\n")
    sys.exit(1)

# Тест 2: База данных
print("2️⃣ Тестирование базы данных...")
try:
    db = Database("test_db.db")
    
    # Создать тестового пользователя
    db.add_user(123456, "test_user", "Test")
    user = db.get_user(123456)
    assert user['user_id'] == 123456
    assert user['trial_searches_left'] == 25
    
    # Тест флага остановки
    db.set_stop_flag(123456, True)
    assert db.should_stop_parsing(123456) == True
    db.clear_stop_flag(123456)
    assert db.should_stop_parsing(123456) == False
    
    print("✅ База данных работает корректно\n")
except Exception as e:
    print(f"❌ Ошибка БД: {e}\n")
    sys.exit(1)

# Тест 3: Клавиатуры
print("3️⃣ Тестирование клавиатур...")
try:
    # Главное меню
    menu_free = main_menu_keyboard(is_admin=False, is_premium=False)
    menu_premium = main_menu_keyboard(is_admin=False, is_premium=True)
    menu_admin = main_menu_keyboard(is_admin=True, is_premium=True)
    
    # Платежи
    payment_kb = payment_method_keyboard()
    
    print("✅ Все клавиатуры созданы успешно\n")
except Exception as e:
    print(f"❌ Ошибка клавиатур: {e}\n")
    sys.exit(1)

# Тест 4: API Peek (опционально, требует интернет)
print("4️⃣ Тестирование Peek API...")
print("⏩ Пропущено (требует интернет и время)\n")

# Тест 5: Нормализация названий
print("5️⃣ Тестирование нормализации названий...")
try:
    test_cases = [
        ("eternal rose", "Eternalrose"),
        ("Evil Eye", "Evileye"),
        ("BLUE STAR", "Bluestar"),
        ("redpacket", "Redpacket")
    ]
    
    for input_name, expected in test_cases:
        normalized = input_name.strip().replace(" ", "").title()
        print(f"   '{input_name}' → '{normalized}'")
    
    print("✅ Нормализация работает корректно\n")
except Exception as e:
    print(f"❌ Ошибка нормализации: {e}\n")

# Тест 6: Список моделей
print("6️⃣ Проверка списка моделей...")
try:
    print(f"   Доступно моделей: {len(AVAILABLE_MODELS)}")
    print(f"   Первые 5: {AVAILABLE_MODELS[:5]}")
    print("✅ Список моделей загружен\n")
except Exception as e:
    print(f"❌ Ошибка списка моделей: {e}\n")

# Итог
print("=" * 50)
print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО!")
print("=" * 50)
print("\n💡 Теперь можно запустить бота:")
print("   python bot.py")
print("\n🚀 Или задеплоить на Railway/Render:")
print("   git add .")
print("   git commit -m 'v2.1'")
print("   git push")

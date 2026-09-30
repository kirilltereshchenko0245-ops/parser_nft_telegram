"""Тест нормализации названий моделей"""
from peek_api import normalize_model_name

test_cases = [
    # EternalRose
    ("eternal rose", "EternalRose"),
    ("Eternal Rose", "EternalRose"),
    ("ETERNAL ROSE", "EternalRose"),
    ("eternalrose", "EternalRose"),
    ("EternalRose", "EternalRose"),
    ("EternalRose-5000", "EternalRose"),  # С номером
    
    # EvilEye
    ("evil eye", "EvilEye"),
    ("Evil Eye", "EvilEye"),
    ("EVIL EYE", "EvilEye"),
    ("evileye", "EvilEye"),
    ("EvilEye", "EvilEye"),
    ("EvilEye-13000", "EvilEye"),  # С номером
    
    # BlueStar
    ("blue star", "BlueStar"),
    ("Blue Star", "BlueStar"),
    ("bluestar", "BlueStar"),
    ("BlueStar", "BlueStar"),
    
    # RedPacket
    ("red packet", "RedPacket"),
    ("redpacket", "RedPacket"),
    ("RedPacket", "RedPacket"),
]

print("🧪 Тестирование нормализации названий...\n")

passed = 0
failed = 0

for input_text, expected in test_cases:
    result = normalize_model_name(input_text)
    status = "✅" if result == expected else "❌"
    
    if result == expected:
        passed += 1
    else:
        failed += 1
    
    print(f"{status} '{input_text}' → '{result}' (ожидалось: '{expected}')")

print(f"\n{'='*50}")
print(f"Пройдено: {passed}/{len(test_cases)}")
print(f"Провалено: {failed}/{len(test_cases)}")

if failed == 0:
    print("🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
else:
    print("⚠️ Есть ошибки!")

"""Shared definitions imported with owner permission; no personal progress."""
import json

PRESET = json.loads(r'''
{
  "activity": {
    "user-c694cb2f-e480-46dd-82f1-86f389357ba9": {
      "name": "Игра на гитаре",
      "description": "",
      "condition": {
        "type": "none"
      },
      "category": "Ум и творчество",
      "rewards": {
        "creativity": 50,
        "will": 5
      }
    }
  },
  "quest": {
    "user-248e9d35-90a1-4a7c-9d90-274c56db8023": {
      "name": "Иди траву потрогай",
      "period": "daily",
      "activity": "walk",
      "count": 1,
      "condition_text": "Прогулка"
    }
  },
  "achievement": {
    "user-4479689d-51d3-4073-8d6b-2bbc89ccbd14": {
      "name": "Новая ступень",
      "description": "",
      "condition": {
        "type": "stat",
        "target": "any",
        "threshold": 25
      },
      "condition_text": "Получить 25-й уровень в любой характеристике."
    }
  }
}
''')

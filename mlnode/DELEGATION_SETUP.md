# Настройка PoC Delegation

Этот документ описывает как настроить делегирование PoC вычислений с маленькой ноды на большую.

## Архитектура

- **Маленькая нода**: 1 GPU, делегирует PoC benchmark на большую ноду
- **Большая нода**: 50-200+ GPU, выполняет PoC вычисления и отправляет батчи обратно
- **Коммуникация**: Отдельный порт 9090 для delegation API
- **Безопасность**: Token-based аутентификация

## Быстрый старт

### 1. Настройка большой ноды

```bash
cd small/deploy/join

# Отредактировать config.env
nano config.env
```

В `config.env` для **большой ноды**:
```env
# Токен аутентификации
export DELEGATION_AUTH_TOKEN=my-secure-token-123

# Максимум сессий (опционально)
export DELEGATION_MAX_SESSIONS=20

# Порт (опционально, по умолчанию 9090)
export DELEGATION_PORT=9090
```

```bash
# 3. Запустить delegation сервис
cd packages/pow/src
python -m pow.service.delegation.app
```

Delegation сервис запустится на порту 9090 и будет принимать запросы от маленьких нод.

### 2. Настройка маленькой ноды

```bash
cd small/deploy/join

# Отредактировать config.env
nano config.env
```

В `config.env` для **маленькой ноды**:
```env
# URL большой ноды
export DELEGATION_URL=http://192.168.1.100:9090

# Токен (должен совпадать с большой нодой)
export DELEGATION_AUTH_TOKEN=my-secure-token-123
```

```bash
# 3. Запустить MLNode как обычно
# Он автоматически переключится в режим делегирования
python -m pow.service.main  # или ваш обычный запуск
```

### 3. Проверка

```bash
# На маленькой ноде - проверить статус
curl http://localhost:8080/api/v1/pow/status

# Ответ должен содержать:
# {
#   "status": "GENERATING",
#   "delegation_mode": true,
#   "gpu_count": 100  # количество GPU на большой ноде
# }
```

## Включение/Выключение делегирования

### ✅ Включить делегирование (маленькая нода)

В `config.env`:
```env
export DELEGATION_URL=http://big-node-ip:9090
export DELEGATION_AUTH_TOKEN=your-token
```

Перезапустить MLNode.

### ❌ Выключить делегирование (локальный режим)

В `config.env` закомментировать или оставить пустыми:
```env
export DELEGATION_URL=
export DELEGATION_AUTH_TOKEN=
```

Или закомментировать:
```env
#export DELEGATION_URL=http://big-node-ip:9090
#export DELEGATION_AUTH_TOKEN=your-token
```

Перезапустить MLNode.

## API Endpoints

### Delegation API (порт 9090, большая нода)

- `POST /api/v1/delegation/start` - Начать сессию делегирования
- `GET /api/v1/delegation/batches/{session_id}` - Получить сгенерированные батчи
- `POST /api/v1/delegation/stop` - Остановить сессию
- `GET /api/v1/delegation/status` - Статус delegation менеджера

### MLNode API (порт 8080, маленькая нода)

Все существующие endpoints работают как обычно:
- `POST /api/v1/pow/init/generate` - Автоматически использует delegation если настроено
- `GET /api/v1/pow/status` - Показывает режим (delegation_mode: true/false)

## Параметры конфигурации

### Маленькая нода

| Переменная | Описание | Пример | По умолчанию |
|------------|----------|--------|--------------|
| `DELEGATION_URL` | URL большой ноды | `http://192.168.1.100:9090` | - (локальный режим) |
| `DELEGATION_AUTH_TOKEN` | Токен аутентификации | `my-secure-token` | - |

### Большая нода

| Переменная | Описание | Пример | По умолчанию |
|------------|----------|--------|--------------|
| `DELEGATION_AUTH_TOKEN` | Токен аутентификации | `my-secure-token` | **обязателен** |
| `DELEGATION_MAX_SESSIONS` | Макс. одновременных сессий | `20` | `10` |
| `DELEGATION_PORT` | Порт delegation API | `9090` | `9090` |
| `DELEGATION_HOST` | Host для binding | `0.0.0.0` | `0.0.0.0` |

## Как это работает

1. **Маленькая нода** получает запрос на PoC benchmark из блокчейна
2. Проверяет `.env` - есть ли `DELEGATION_URL` и `DELEGATION_AUTH_TOKEN`
3. **Если да** → создает `DelegationClient` вместо `ParallelController`
4. `DelegationClient` отправляет POST запрос на `http://big-node:9090/api/v1/delegation/start`
5. **Большая нода** создает сессию, запускает `ParallelController` со всеми своими GPU
6. `DelegationClient` каждые 5 секунд опрашивает `GET /api/v1/delegation/batches/{session_id}`
7. **Большая нода** отправляет сгенерированные батчи
8. **Маленькая нода** получает батчи, подписывает своим приватным ключом, отправляет в блокчейн
9. Через 5.5 минут сессия автоматически истекает

## Безопасность

- **Токен аутентификации**: Все запросы требуют валидный `auth_token`
- **Приватный ключ**: Остается только на маленькой ноде, большая нода НЕ может подписывать транзакции
- **Изоляция**: Delegation API на отдельном порту (9090) от основного API (8080)
- **Таймауты**: Сессии автоматически закрываются через 5.5 минут

## Мониторинг

### Проверить статус delegation менеджера (большая нода)
```bash
curl http://big-node:9090/api/v1/delegation/status
```

Ответ:
```json
{
  "total_sessions": 5,
  "active_sessions": 3,
  "max_sessions": 10,
  "sessions": {
    "uuid-1": {
      "status": "generating",
      "node_id": 0,
      "gpu_count": 100,
      "total_generated": 50,
      "age_seconds": 120.5
    },
    ...
  }
}
```

### Проверить статус PoW (маленькая нода)
```bash
curl http://localhost:8080/api/v1/pow/status
```

Ответ (delegation mode):
```json
{
  "status": "GENERATING",
  "is_model_initialized": true,
  "delegation_mode": true,
  "gpu_count": 100
}
```

Ответ (local mode):
```json
{
  "status": "GENERATING",
  "is_model_initialized": true,
  "delegation_mode": false
}
```

## Troubleshooting

### Маленькая нода не переключается в delegation mode

**Проблема**: Статус показывает `delegation_mode: false`

**Решение**:
1. Проверьте `deploy/join/config.env` - установлены ли `DELEGATION_URL` и `DELEGATION_AUTH_TOKEN`
2. Убедитесь что config.env загружен (source config.env перед запуском)
3. Перезапустите MLNode
4. Проверьте логи: `grep "DELEGATION" logs/mlnode.log`

### Ошибка подключения к большой ноде

**Проблема**: `Failed to start delegation session: Connection refused`

**Решение**:
1. Проверьте что delegation сервис запущен на большой ноде
2. Проверьте URL в `DELEGATION_URL` (правильный IP и порт)
3. Проверьте firewall/network между нодами
4. Проверьте что порт 9090 открыт

### Ошибка аутентификации

**Проблема**: `Invalid auth token`

**Решение**:
1. Проверьте что `DELEGATION_AUTH_TOKEN` одинаковый на обеих нодах
2. Убедитесь что нет лишних пробелов в токене
3. Перезапустите delegation сервис после изменения токена

### Validation endpoints не работают

**Проблема**: `Validation phase not supported in delegation mode`

**Решение**:
- Это нормально! В delegation mode validation не поддерживается
- Большая нода только генерирует батчи
- Маленькая нода только подписывает и отправляет в блокчейн

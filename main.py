import random
import string
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse

app = FastAPI()

# Плейлисты с точными мегахитами, которые знают абсолютно все
PLAYLISTS = {
    "ru_top_2025_2026": [
        "Instasamka За деньги да", "MACAN Asphalt 8", "MACAN IVORY", "ANIKV & Aarne Procedural", 
        "OG Buda Бандит", "Mayot Море", "Три Дня Дождя Гори", "Aarne & Bushido Zho Heavy Metal", 
        "Asti По барам", "HammAli & Navai Птичка", "Niletto Любимка", "Slava Marlow Ты горишь как огонь",
        "Zivert Life", "Gayazovs Brothers По синей грусти", "Instasamka Lipsi Ha", "MACAN Дорога",
        "Anna Asti Царица", "Jony Комета", "Xcho Raze", "Miyagi & Эндшпиль I Got Love", "Artik & Asti Девочка танцуй"
    ],
    "ru_hits": [
        "Баста Сансара", "Руки Вверх Крошка моя", "Звери Районы-кварталы", "Кино Группа крови", 
        "Меладзе Сэра", "Zivert Credo", "Корж Малый повзрослел", "Агутин На сиреневой луне", 
        "МакSим Знаешь ли ты", "Жуки Батарейка", "Ленинград Экспонат", "Сплин Мое сердце", 
        "Би-2 Полковнику никто не пишет", "Мумий Тролль Невеста", "Дискотека Авария Малинки", 
        "Иванушки International Тополиный пух", "Чайф Оранжевое настроение", "Земфира Искала"
    ],
    "pop_world": [
        "The Weeknd Blinding Lights", "Dua Lipa Levitating", "Lady Gaga Bad Romance", 
        "Rihanna Umbrella", "Bruno Mars Uptown Funk", "Billie Eilish Bad Guy", 
        "Michael Jackson Billie Jean", "Madonna Hung Up", "Ed Sheeran Shape of You", 
        "Justin Bieber Baby", "Ariana Grande 7 rings", "Taylor Swift Blank Space", 
        "Miley Cyrus Flowers", "Katy Perry Roar", "Harry Styles As It Was", "Post Malone Sunflower"
    ],
    "rock": [
        "Linkin Park In the End", "Nirvana Smells Like Teen Spirit", "Queen Bohemian Rhapsody", 
        "Король и Шут Прыгну со скалы", "Сплин Выхода нет", "Би-2 Варвара", "Imagine Dragons Radioactive", 
        "Rammstein Du Hast", "AC DC Back in Black", "Metallica Enter Sandman", "Scorpions Wind of Change", 
        "Muse Supermassive Black Hole", "Green Day American Idiot", "Red Hot Chili Peppers Californication"
    ],
    "pop_90s": [
        "Руки Вверх 18 мне уже", "Иванушки International Кукла", "Демо Солнышко", "Краски Он не знал", 
        "Стрелки На вечеринке", "Виктор Цой Пачка сигарет", "На-На Фаина", "Кар Мен Лондон гуд бай", 
        "Линда Ворона", "Гости из Будущего Беги от меня", "Отпетые Мошенники Люби меня люби", 
        "Вирус Ты меня не ищи", "Пропаганда Мелом", "Тату Нас не догонят", "Шура Холодная луна", "Андрей Губин Мальчик-бродяга"
    ],
    "soundtracks": [
        "Hans Zimmer Interstellar", "Ennio Morricone Chi Mai", "Daft Punk Starboy", 
        "Harry Potter Hedwig's Theme", "Pirates of the Caribbean He's a Pirate", 
        "Lord of the Rings Concerning Hobbits", "Gladiator Now We Are Free", "Cypis Gdzie jest biały węgorz"
    ]
}

QUESTIONS = [
    "В какой ситуации или месте идеально слушать этот трек?",
    "Если бы эта песня была сценой из фильма, что бы там происходило?",
    "Опиши настроение этой песни тремя прилагательными.",
    "Какой цвет или время года лучше всего подходят этому треку?",
    "Кто из известных персонажей мог бы слушать эту песню на повторе?"
]

rooms = {}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def generate_room_code() -> str:
    """Генерирует уникальный 4-значный код комнаты."""
    return "".join(random.choices(string.digits, k=4))

async def fetch_tracks(playlist_key: str, count: int = 15) -> list:
    """Загружает треки через iTunes API на основе выбранного плейлиста."""
    queries = PLAYLISTS.get(playlist_key, PLAYLISTS["ru_top_2025_2026"])
    selected_queries = random.sample(queries, min(len(queries), count))
    tracks = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for q in selected_queries:
            url = f"https://itunes.apple.com/search?term={q}&country=ru&entity=song&limit=5"
            try:
                resp = await client.get(url)
                data = resp.json()
                results = [r for r in data.get("results", []) if r.get("previewUrl")]
                if results:
                    song = results[0]
                    track_data = {
                        "id": str(song["trackId"]),
                        "title": f"{song['artistName']} — {song['trackName']}",
                        "preview_url": song["previewUrl"],
                        "artwork": song.get("artworkUrl100", "")
                    }
                    if track_data not in tracks:
                        tracks.append(track_data)
            except Exception:
                continue
    
    random.shuffle(tracks)
    return tracks

async def broadcast(room_code: str, message: dict):
    """Отправляет JSON-сообщение всем игрокам в комнате."""
    if room_code not in rooms:
        return
    for player in rooms[room_code]["players"].values():
        try:
            await player["ws"].send_json(message)
        except Exception:
            pass

async def broadcast_room_state(room_code: str):
    """Рассылает актуальное состояние комнаты (игроки, очки, статусы)."""
    if room_code not in rooms:
        return
    room = rooms[room_code]
    players_public = [
        {
            "id": uid,
            "name": p["name"],
            "photo": p.get("photo", ""),
            "score": p["score"],
            "ready": p["ready"],
            "is_host": uid == room["host_id"]
        }
        for uid, p in room["players"].items()
    ]
    await broadcast(room_code, {
        "type": "room_state",
        "room_code": room_code,
        "state": room["state"],
        "host_id": room["host_id"],
        "players": players_public
    })

# --- МАРШРУТЫ И WEBSOCKET ---

@app.get("/")
async def get_index():
    return FileResponse("index.html")

@app.websocket("/ws/{room_code}/{user_id}/{user_name}")
async def websocket_endpoint(
    websocket: WebSocket,
    room_code: str,
    user_id: str,
    user_name: str,
    photo: str = Query(default="")
):
    await websocket.accept()

    # Создание новой комнаты
    if room_code == "CREATE":
        room_code = generate_room_code()
        while room_code in rooms:
            room_code = generate_room_code()
        rooms[room_code] = {
            "host_id": user_id,
            "state": "lobby",
            "playlist": "ru_top_2025_2026",
            "players": {},
            "spy_id": None,
            "secret_track": None,
            "spy_track": None,
            "track_options": [],
            "votes": {}
        }
    elif room_code not in rooms:
        await websocket.send_json({"type": "error", "message": "Комната не найдена!"})
        await websocket.close()
        return

    room = rooms[room_code]
    existing_score = room["players"][user_id]["score"] if user_id in room["players"] else 0
    
    # Добавление игрока
    room["players"][user_id] = {
        "ws": websocket,
        "name": user_name,
        "photo": photo,
        "score": existing_score,
        "ready": False
    }

    await broadcast_room_state(room_code)

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            # Переключение готовности
            if action == "toggle_ready":
                room["players"][user_id]["ready"] = True
                await broadcast_room_state(room_code)

            # Старт игры
            elif action == "start_game" and user_id == room["host_id"]:
                if len(room["players"]) < 2:
                    await websocket.send_json({"type": "error", "message": "Нужно минимум 3 игрока (для теста от 2)!"})
                    continue

                playlist = data.get("playlist", "ru_top_2025_2026")
                tracks = await fetch_tracks(playlist, count=12)
                if len(tracks) < 2:
                    await websocket.send_json({"type": "error", "message": "Ошибка загрузки музыки, попробуйте еще раз."})
                    continue

                room["state"] = "listening"
                room["track_options"] = tracks[:8]
                
                selected_two = random.sample(tracks, 2)
                room["secret_track"] = selected_two[0]
                room["spy_track"] = selected_two[1]

                room["spy_id"] = random.choice(list(room["players"].keys()))
                room["votes"] = {}
                question = random.choice(QUESTIONS)

                for uid, p in room["players"].items():
                    is_spy = (uid == room["spy_id"])
                    await p["ws"].send_json({
                        "type": "game_started",
                        "is_spy": is_spy,
                        "track": room["spy_track"] if is_spy else room["secret_track"],
                        "question": question
                    })

            # Переход к голосованию
            elif action == "start_voting" and user_id == room["host_id"]:
                room["state"] = "voting"
                await broadcast(room_code, {"type": "voting_started"})

            # Обработка голосов
            elif action == "vote":
                target_id = data.get("target_id")
                room["votes"][user_id] = target_id

                if len(room["votes"]) == len(room["players"]):
                    vote_counts = {}
                    for v in room["votes"].values():
                        vote_counts[v] = vote_counts.get(v, 0) + 1
                    
                    most_voted_id = max(vote_counts, key=vote_counts.get)
                    spy_id = room["spy_id"]
                    spy_name = room["players"][spy_id]["name"]
                    voted_name = room["players"][most_voted_id]["name"]

                    if most_voted_id == spy_id:
                        room["state"] = "spy_guess"
                        await broadcast(room_code, {
                            "type": "spy_caught",
                            "spy_id": spy_id,
                            "spy_name": spy_name,
                            "options": [{"id": t["id"], "title": t["title"]} for t in room["track_options"]]
                        })
                    else:
                        room["players"][spy_id]["score"] += 2
                        room["state"] = "results"
                        await broadcast_room_state(room_code)
                        await broadcast(room_code, {
                            "type": "game_over",
                            "winner": "spy",
                            "reason": f"Игроки ошиблись и обвинили {voted_name}! Шпионом был {spy_name} (+2 очка).",
                            "secret_track": room["secret_track"]["title"]
                        })

            # Попытка шпиона угадать трек
            elif action == "spy_guess_track" and user_id == room["spy_id"]:
                guessed_id = data.get("track_id")
                spy_id = room["spy_id"]
                spy_name = room["players"][spy_id]["name"]
                
                if guessed_id == room["secret_track"]["id"]:
                    room["players"][spy_id]["score"] += 2
                    reason = f"Шпион ({spy_name}) рассекречен, но угадал трек и украл победу (+2 очка)!"
                    winner = "spy"
                else:
                    for uid, p in room["players"].items():
                        if uid != spy_id:
                            p["score"] += 2
                    reason = f"Шпион ({spy_name}) пойман и не смог угадать трек! Мирные получают по +2 очка."
                    winner = "civilians"

                room["state"] = "results"
                await broadcast_room_state(room_code)
                await broadcast(room_code, {
                    "type": "game_over",
                    "winner": winner,
                    "reason": reason,
                    "secret_track": room["secret_track"]["title"]
                })

            # Возврат в лобби
            elif action == "back_to_lobby" and user_id == room["host_id"]:
                room["state"] = "lobby"
                for p in room["players"].values():
                    p["ready"] = False
                await broadcast_room_state(room_code)

    except WebSocketDisconnect:
        if room_code in rooms and user_id in rooms[room_code]["players"]:
            del rooms[room_code]["players"][user_id]
            if not rooms[room_code]["players"]:
                del rooms[room_code]
            else:
                if rooms[room_code]["host_id"] == user_id:
                    rooms[room_code]["host_id"] = next(iter(rooms[room_code]["players"]))
                await broadcast_room_state(room_code)

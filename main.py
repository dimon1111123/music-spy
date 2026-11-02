import random
import string
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse

app = FastAPI()

PLAYLISTS = {
    "ru_hits": ["Баста", "Руки Вверх", "Звери", "Кино", "Меладзе", "Zivert", "Корж", "Агутин", "МакSим", "Жуки"],
    "pop_world": ["The Weeknd", "Dua Lipa", "Lady Gaga", "Rihanna", "Bruno Mars", "Billie Eilish", "Michael Jackson", "Madonna"],
    "rock": ["Linkin Park", "Nirvana", "Queen", "Король и Шут", "Сплин", "Би-2", "Imagine Dragons", "Rammstein"],
    "pop_90s": ["Руки Вверх", "Иванушки International", "Демо", "Краски", "Стрелки", "Виктор Цой", "На-На"],
    "soundtracks": ["Hans Zimmer", "Ennio Morricone", "Interstellar theme", "Daft Punk", "Harry Potter soundtrack", "Pirates of the Caribbean"]
}

QUESTIONS = [
    "В какой ситуации или месте идеально слушать этот трек?",
    "Если бы эта песня была сценой из фильма, что бы там происходило?",
    "Опиши настроение этой песни тремя прилагательными.",
    "Какой цвет или время года лучше всего подходят этому треку?",
    "Кто из известных персонажей мог бы слушать эту песню на повторе?"
]

rooms = {}

def generate_room_code():
    return ''.join(random.choices(string.digits, k=4))

async def fetch_tracks(playlist_key: str, count: int = 8):
    queries = PLAYLISTS.get(playlist_key, PLAYLISTS["ru_hits"])
    selected_queries = random.sample(queries, min(len(queries), count))
    tracks = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for q in selected_queries:
            url = f"https://itunes.apple.com/search?term={q}&country=ru&entity=song&limit=15"
            try:
                resp = await client.get(url)
                data = resp.json()
                results = [r for r in data.get("results", []) if r.get("previewUrl")]
                if results:
                    song = random.choice(results)
                    tracks.append({
                        "id": str(song["trackId"]),
                        "title": f"{song['artistName']} — {song['trackName']}",
                        "preview_url": song["previewUrl"],
                        "artwork": song.get("artworkUrl100", "")
                    })
            except Exception:
                continue
    return tracks

async def broadcast(room_code: str, message: dict):
    if room_code not in rooms:
        return
    room = rooms[room_code]
    for uid, player in list(room["players"].items()):
        try:
            await player["ws"].send_json(message)
        except Exception:
            pass

async def broadcast_room_state(room_code: str):
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

    if room_code == "CREATE":
        room_code = generate_room_code()
        while room_code in rooms:
            room_code = generate_room_code()
        rooms[room_code] = {
            "host_id": user_id,
            "state": "lobby",
            "playlist": "ru_hits",
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
    # Если игрок подключается заново, сохраняем его очки, иначе 0
    existing_score = room["players"][user_id]["score"] if user_id in room["players"] else 0
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

            if action == "toggle_ready":
                room["players"][user_id]["ready"] = True
                await broadcast_room_state(room_code)

            elif action == "start_game" and user_id == room["host_id"]:
                if len(room["players"]) < 2:
                    await websocket.send_json({"type": "error", "message": "Нужно минимум 3 игрока (для теста от 2)!"})
                    continue

                playlist = data.get("playlist", "ru_hits")
                tracks = await fetch_tracks(playlist, count=6)
                if len(tracks) < 2:
                    await websocket.send_json({"type": "error", "message": "Ошибка загрузки музыки, попробуйте еще раз."})
                    continue

                room["state"] = "listening"
                room["track_options"] = tracks
                
                # Выбираем основной трек и другой трек для шпиона
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

            elif action == "start_voting" and user_id == room["host_id"]:
                room["state"] = "voting"
                await broadcast(room_code, {"type": "voting_started"})

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
                        # Шпиона поймали — даем ему шанс угадать песню
                        room["state"] = "spy_guess"
                        await broadcast(room_code, {
                            "type": "spy_caught",
                            "spy_id": spy_id,
                            "spy_name": spy_name,
                            "options": [{"id": t["id"], "title": t["title"]} for t in room["track_options"]]
                        })
                    else:
                        # Мирные ошиблись — Шпион победил (+2 очка Шпиону)
                        room["players"][spy_id]["score"] += 2
                        room["state"] = "results"
                        await broadcast_room_state(room_code)
                        await broadcast(room_code, {
                            "type": "game_over",
                            "winner": "spy",
                            "reason": f"Игроки ошиблись и обвинили {voted_name}! Шпионом был {spy_name} (+2 очка).",
                            "secret_track": room["secret_track"]["title"]
                        })

            elif action == "spy_guess_track" and user_id == room["spy_id"]:
                guessed_id = data.get("track_id")
                spy_id = room["spy_id"]
                spy_name = room["players"][spy_id]["name"]
                
                if guessed_id == room["secret_track"]["id"]:
                    # Шпион угадал трек (+2 очка Шпиону)
                    room["players"][spy_id]["score"] += 2
                    reason = f"Шпион ({spy_name}) рассекречен, но угадал трек и украл победу (+2 очка)!"
                    winner = "spy"
                else:
                    # Шпион не угадал (+2 очка всем мирным)
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

import os
import json
import time
from datetime import datetime
from typing import Any

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from openai import OpenAI
from dotenv import load_dotenv


load_dotenv()

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = (
    "playlist-read-private "
    "playlist-read-collaborative "
    "playlist-modify-private "
    "playlist-modify-public"
)
DEMO_PLAYLIST_DESCRIPTION = "Playlist de prueba creada desde Spotipy."
DEMO_PLAYLIST_PREFIX = "Spotify AI Agent - Demo"
DEMO_TRACK_QUERIES = [
    "Blinding Lights The Weeknd",
    "Take On Me a-ha",
    "Blue Monday New Order",
]
AI_PLAYLIST_PREFIX = "Spotify AI Agent - AI"
AI_TRACK_COUNT_DEFAULT = 25
AI_MODEL_DEFAULT = "gpt-4o-mini"
OPENAI_TIMEOUT_DEFAULT = 20
OPENAI_MAX_RETRIES_DEFAULT = 0
SPOTIFY_TIMEOUT_DEFAULT = 5
SPOTIFY_MARKET_DEFAULT = ""
SPOTIFY_SLEEP_MS_DEFAULT = 200
SPOTIFY_MAX_RETRY_AFTER_DEFAULT = 5
SPOTIFY_VERIFY_AFTER_MS_DEFAULT = 1500
SPOTIFY_VERIFY_RETRIES_DEFAULT = 3
LAST_RUN_FILE = "last_run.json"
FALLBACK_SONGS_FILE = "songs.txt"
INLINE_TEST_SONGS = [
    "A-ha - Take On Me",
    "The Cure - Just Like Heaven",
    "Depeche Mode - Enjoy the Silence",
]
INLINE_TEST_URIS = [
    "spotify:track:2GMN0Jk8sG8B7P5b4OR45R",
    "spotify:track:1JSTJqkT5qHq8MDJnJbRE1",
    "spotify:track:7vL1FQ63R90aS6hGvrq0tX",
]


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. "
            "Configura tus credenciales de Spotify antes de continuar."
        )
    return value


def get_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def get_bool_env(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def sleep_ms(value: int) -> None:
    if value > 0:
        time.sleep(value / 1000.0)


def get_retry_after_seconds(exc: SpotifyException) -> int | None:
    try:
        header_value = exc.headers.get("Retry-After") if exc.headers else None
        return int(header_value) if header_value else None
    except (ValueError, TypeError):
        return None


def build_client() -> spotipy.Spotify:
    client_id = get_env("SPOTIFY_CLIENT_ID")
    client_secret = get_env("SPOTIFY_CLIENT_SECRET")
    redirect_uri = get_env("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        open_browser=True,
    )
    return spotipy.Spotify(
        auth_manager=auth_manager,
        requests_timeout=SPOTIFY_TIMEOUT_DEFAULT,
        retries=0,
        status_retries=0,
        backoff_factor=0,
        status_forcelist=(),
    )


def print_playlists(data: dict[str, Any]) -> None:
    items = data.get("items", [])
    if not items:
        print("No tienes playlists visibles en esta cuenta.")
        return

    print("\nPlaylists:")
    for playlist in items:
        name = playlist.get("name", "Sin nombre")
        tracks = playlist.get("tracks", {}).get("total", 0)
        owner = playlist.get("owner", {}).get("display_name", "Sin propietario")
        print(f"- {name} | {tracks} canciones | owner: {owner}")


def build_demo_playlist_name() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"{DEMO_PLAYLIST_PREFIX} {timestamp}"


def build_ai_playlist_name(prompt: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    short_prompt = " ".join(prompt.strip().split())[:80]
    return f"{AI_PLAYLIST_PREFIX} {short_prompt} {timestamp}"


def build_fallback_playlist_name() -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"Spotify AI Agent - Fallback {timestamp}"


def find_existing_demo_playlist(
    spotify: spotipy.Spotify, playlists: dict[str, Any]
) -> dict[str, Any] | None:
    items = playlists.get("items", [])
    for playlist in items:
        name = playlist.get("name", "")
        if name.startswith(DEMO_PLAYLIST_PREFIX):
            return playlist
    return None


def create_demo_playlist(spotify: spotipy.Spotify) -> dict[str, Any]:
    return spotify.current_user_playlist_create(
        name=build_demo_playlist_name(),
        public=True,
        collaborative=False,
        description=DEMO_PLAYLIST_DESCRIPTION,
    )


def get_or_create_demo_playlist(
    spotify: spotipy.Spotify, playlists: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    existing = find_existing_demo_playlist(spotify, playlists)
    if existing:
        return existing, False
    return create_demo_playlist(spotify), True


def search_track_uri(spotify: spotipy.Spotify, query: str) -> str | None:
    normalized = normalize_text(query)
    search_query = build_spotify_query(normalized)
    market = os.getenv("SPOTIFY_MARKET", SPOTIFY_MARKET_DEFAULT).strip()
    debug = get_bool_env("SPOTIFY_DEBUG")

    def _search(q: str) -> list[dict[str, Any]]:
        if market:
            results = spotify.search(q=q, type="track", limit=1, market=market)
        else:
            results = spotify.search(q=q, type="track", limit=1)
        items = results.get("tracks", {}).get("items", [])
        if debug:
            print(f"Spotify debug -> q='{q}' market='{market or 'none'}' resultados={len(items)}")
        return items

    try:
        items = _search(search_query)
    except SpotifyException as exc:
        if exc.http_status == 429:
            raise
        return None
    if items:
        if debug:
            print(f"Spotify debug -> uri encontrada: {items[0].get('uri')}")
        return items[0].get("uri")

    if search_query != normalized:
        try:
            items = _search(normalized)
        except SpotifyException as exc:
            if exc.http_status == 429:
                raise
            return None
        if items:
            if debug:
                print(f"Spotify debug -> uri encontrada: {items[0].get('uri')}")
            return items[0].get("uri")
    return None


def add_demo_tracks(spotify: spotipy.Spotify, playlist_id: str) -> list[str]:
    added_queries: list[str] = []
    for query in DEMO_TRACK_QUERIES:
        track_uri = search_track_uri(spotify, query)
        if not track_uri:
            continue
        spotify.playlist_add_items(playlist_id, [track_uri])
        added_queries.append(query)
    return added_queries


def get_prompt_from_user() -> str:
    prompt = os.getenv("PLAYLIST_PROMPT", "").strip()
    if prompt:
        return prompt
    return input("\nDescribe tu playlist (Enter para usar demo): ").strip()


def parse_track_list(raw_text: str, count: int) -> list[str]:
    content = (raw_text or "").strip()
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()][:count]
    except json.JSONDecodeError:
        pass

    lines = [line.strip("- ").strip() for line in content.splitlines()]
    lines = [line for line in lines if line]
    return lines[:count]


def normalize_text(text: str) -> str:
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
    }
    return "".join(replacements.get(ch, ch) for ch in text).strip()


def build_spotify_query(query: str) -> str:
    if " - " not in query:
        return query
    artist, track = query.split(" - ", 1)
    artist = artist.strip()
    track = track.strip()
    if not artist or not track:
        return query
    return f'track:"{track}" artist:"{artist}"'


def generate_tracks_with_openai(prompt: str, count: int) -> list[str]:
    api_key = get_env("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", AI_MODEL_DEFAULT).strip() or AI_MODEL_DEFAULT
    timeout = get_int_env("OPENAI_TIMEOUT", OPENAI_TIMEOUT_DEFAULT)
    max_retries = get_int_env("OPENAI_MAX_RETRIES", OPENAI_MAX_RETRIES_DEFAULT)
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
    system_message = (
        "Devuelve SOLO un JSON valido con un array de strings. "
        "Cada string debe tener el formato 'Titulo - Artista'. "
        f"Maximo {count} elementos."
    )
    user_message = f"Genero una lista para: {prompt}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        temperature=0.7,
    )
    return parse_track_list(response.choices[0].message.content or "", count)


def load_fallback_songs() -> list[str]:
    try:
        with open(FALLBACK_SONGS_FILE, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle.readlines()]
    except OSError:
        return []

    return [line for line in lines if line]


def dedupe_queries(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        key = query.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(query.strip())
    return result


def get_playlist_track_uris(spotify: spotipy.Spotify, playlist_id: str) -> set[str]:
    uris: set[str] = set()
    offset = 0
    while True:
        page = spotify.playlist_items(
            playlist_id,
            limit=100,
            offset=offset,
            fields="items(track(uri)),next",
        )
        for item in page.get("items", []):
            track = item.get("track")
            if track and track.get("uri"):
                uris.add(track["uri"])
        if not page.get("next"):
            break
        offset += 100
    return uris


def add_tracks_from_queries(
    spotify: spotipy.Spotify, playlist_id: str, queries: list[str]
) -> dict[str, list[str]]:
    found: list[str] = []
    not_found: list[str] = []
    uris: list[str] = []

    queries = dedupe_queries(queries)
    existing_uris = get_playlist_track_uris(spotify, playlist_id)
    before_total = None
    try:
        before_total = (
            spotify.playlist(playlist_id, fields="tracks.total")
            .get("tracks", {})
            .get("total")
        )
    except Exception:
        before_total = None
    direct_uris_raw = os.getenv("SPOTIFY_DIRECT_URIS", "").strip()
    if get_bool_env("INLINE_TEST_URIS"):
        direct_uris = INLINE_TEST_URIS[:]
    elif direct_uris_raw:
        direct_uris = [u.strip() for u in direct_uris_raw.split(",") if u.strip()]
    else:
        direct_uris = []

    if direct_uris:
        uris = [u for u in direct_uris if u not in existing_uris]
        added: list[str] = []
        for i in range(0, len(uris), 100):
            batch = uris[i : i + 100]
            if not batch:
                continue
            spotify.playlist_add_items(playlist_id, batch)
            added.extend(batch)
        # Verify what actually landed in the playlist (eventual consistency)
        verify_after = get_int_env("SPOTIFY_VERIFY_AFTER_MS", SPOTIFY_VERIFY_AFTER_MS_DEFAULT)
        verify_retries = get_int_env("SPOTIFY_VERIFY_RETRIES", SPOTIFY_VERIFY_RETRIES_DEFAULT)
        verified: list[str] = []
        for _ in range(max(1, verify_retries)):
            sleep_ms(verify_after)
            current_uris = get_playlist_track_uris(spotify, playlist_id)
            verified = [u for u in added if u in current_uris]
            if len(verified) == len(added):
                break
        if len(verified) != len(added):
            print(
                f"Advertencia: se intentaron añadir {len(added)} URIs, "
                f"pero Spotify muestra {len(verified)} en la playlist."
            )
        after_total = None
        try:
            after_total = (
                spotify.playlist(playlist_id, fields="tracks.total")
                .get("tracks", {})
                .get("total")
            )
        except Exception:
            after_total = None
        return {
            "found": queries,
            "not_found": [],
            "added": verified,
            "before_total": before_total,
            "after_total": after_total,
        }

    rate_limited = False
    sleep_ms_value = get_int_env("SPOTIFY_SLEEP_MS", SPOTIFY_SLEEP_MS_DEFAULT)
    max_retry_after = get_int_env(
        "SPOTIFY_MAX_RETRY_AFTER", SPOTIFY_MAX_RETRY_AFTER_DEFAULT
    )
    for query in queries:
        sleep_ms(sleep_ms_value)
        try:
            track_uri = search_track_uri(spotify, query)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = get_retry_after_seconds(exc)
                if retry_after is not None and retry_after <= max_retry_after:
                    sleep_ms(retry_after * 1000)
                    try:
                        track_uri = search_track_uri(spotify, query)
                    except SpotifyException as retry_exc:
                        if retry_exc.http_status == 429:
                            rate_limited = True
                            break
                        track_uri = None
                else:
                    rate_limited = True
                    break
            else:
                track_uri = None
            if rate_limited:
                break
        if track_uri:
            found.append(query)
            if track_uri not in existing_uris:
                uris.append(track_uri)
                existing_uris.add(track_uri)
        else:
            not_found.append(query)

    added: list[str] = []
    for i in range(0, len(uris), 100):
        batch = uris[i : i + 100]
        if not batch:
            continue
        sleep_ms(sleep_ms_value)
        try:
            spotify.playlist_add_items(playlist_id, batch)
        except SpotifyException as exc:
            if exc.http_status == 429:
                retry_after = get_retry_after_seconds(exc)
                if retry_after is not None and retry_after <= max_retry_after:
                    sleep_ms(retry_after * 1000)
                    try:
                        spotify.playlist_add_items(playlist_id, batch)
                    except SpotifyException as retry_exc:
                        if retry_exc.http_status == 429:
                            rate_limited = True
                            break
                        raise
                else:
                    rate_limited = True
                    break
            raise
        added.extend(found[i : i + len(batch)])

    if rate_limited:
        print("Spotify rate limit alcanzado. Se detuvo la insercion para evitar espera.")
    after_total = None
    try:
        after_total = (
            spotify.playlist(playlist_id, fields="tracks.total")
            .get("tracks", {})
            .get("total")
        )
    except Exception:
        after_total = None
    return {
        "found": found,
        "not_found": not_found,
        "added": added,
        "before_total": before_total,
        "after_total": after_total,
    }


def save_last_run(payload: dict[str, Any]) -> None:
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        with open(LAST_RUN_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def main() -> None:
    try:
        spotify = build_client()
        user = spotify.current_user()
        playlists = spotify.current_user_playlists(limit=20)
    except Exception as exc:
        print(f"Error al conectar con Spotify: {exc}")
        return

    display_name = user.get("display_name") or user.get("id", "usuario desconocido")
    print(f"Usuario autenticado: {display_name}")
    print_playlists(playlists)

    prompt = get_prompt_from_user()
    if prompt:
        ai_queries: list[str] = []
        used_fallback = False
        source_label = "IA"
        try:
            count = get_int_env("AI_TRACK_COUNT", AI_TRACK_COUNT_DEFAULT)
            if get_bool_env("INLINE_TEST"):
                ai_queries = INLINE_TEST_SONGS[:]
                used_fallback = True
                source_label = "INLINE_TEST"
            else:
                ai_queries = generate_tracks_with_openai(prompt, count)
                source_label = "OpenAI"
        except Exception as exc:
            print(f"\nError con OpenAI: {exc}")
            print("Usando modo fallback desde songs.txt.")
            ai_queries = load_fallback_songs()
            used_fallback = True

        ai_queries = dedupe_queries(ai_queries)
        ai_queries = ai_queries[:count]
        if not ai_queries:
            print("\nNo hay canciones disponibles para usar.")
            return

        if used_fallback:
            print("\nCanciones cargadas desde songs.txt:")
        else:
            print(f"\nCanciones generadas por IA ({source_label}):")
        for query in ai_queries:
            print(f"- {query}")

        try:
            ai_playlist = spotify.current_user_playlist_create(
                name=build_fallback_playlist_name()
                if used_fallback
                else build_ai_playlist_name(prompt),
                public=True,
                collaborative=False,
                description=(
                    "Playlist generada desde songs.txt (fallback)."
                    if used_fallback
                    else f"Playlist generada por IA para: {prompt}"
                ),
            )
        except Exception as exc:
            print(f"\nNo se pudo crear la playlist de IA: {exc}")
            return

        ai_playlist_id = ai_playlist.get("id")
        ai_playlist_url = ai_playlist.get("external_urls", {}).get("spotify", "sin URL")
        if not ai_playlist_id:
            print("No se pudo obtener el ID de la playlist de IA.")
            return

        try:
            results = add_tracks_from_queries(spotify, ai_playlist_id, ai_queries)
        except Exception as exc:
            print(f"\nNo se pudieron agregar canciones de IA: {exc}")
            return

        print("\nCanciones encontradas en Spotify:")
        for query in results["found"]:
            print(f"- {query}")

        if results["not_found"]:
            print("\nCanciones no encontradas:")
            for query in results["not_found"]:
                print(f"- {query}")

        print("\nCanciones anadidas:")
        for query in results["added"]:
            print(f"- {query}")

        print(f"\nURL de la playlist creada: {ai_playlist_url}")
        before_total = results.get("before_total")
        after_total = results.get("after_total")
        if before_total is not None and after_total is not None:
            print(f"Total en Spotify: antes={before_total} despues={after_total}")
        if not results["added"] and before_total is not None and after_total is not None:
            if after_total > before_total:
                print(
                    "Spotify anadio canciones, pero no se pudieron mapear a la lista."
                )
            else:
                print("No se agregaron canciones. Revisa Spotify o la busqueda.")
        save_last_run(
            {
                "prompt": prompt,
                "playlist_id": ai_playlist_id,
                "playlist_url": ai_playlist_url,
                "generated": ai_queries,
                "found": results["found"],
                "not_found": results["not_found"],
                "added": results["added"],
            }
        )
        return

    try:
        demo_playlist, created = get_or_create_demo_playlist(spotify, playlists)
    except Exception as exc:
        print(f"\nNo se pudo crear la playlist de prueba: {exc}")
        return

    playlist_name = demo_playlist.get("name", "Spotify AI Agent - Demo")
    playlist_url = demo_playlist.get("external_urls", {}).get("spotify", "sin URL")
    playlist_id = demo_playlist.get("id")

    if created:
        print(f"\nPlaylist de prueba creada: {playlist_name}")
    else:
        print(f"\nPlaylist demo reutilizada: {playlist_name}")
    print(f"URL: {playlist_url}")

    if not playlist_id:
        print("No se pudo obtener el ID de la playlist demo.")
        return

    try:
        added_tracks = add_demo_tracks(spotify, playlist_id)
    except Exception as exc:
        print(f"No se pudieron agregar canciones de prueba: {exc}")
        return

    if added_tracks:
        print("\nCanciones de prueba agregadas:")
        for query in added_tracks:
            print(f"- {query}")
    else:
        print("\nNo se encontro ninguna cancion de prueba para agregar.")


if __name__ == "__main__":
    main()

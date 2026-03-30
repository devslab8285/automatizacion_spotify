import os
import re
from typing import Iterable, List

import spotipy
import streamlit as st
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler

from main import (
    SCOPE,
    add_tracks_from_queries,
    build_ai_playlist_name,
    build_fallback_playlist_name,
    dedupe_queries,
    generate_tracks_with_openai,
    load_fallback_songs,
    normalize_text,
)


st.set_page_config(page_title="Spotify AI Agent", layout="centered")
st.title("Spotify AI Agent")
st.caption("Crea playlists en Spotify desde un prompt, un archivo o una lista pegada.")


def _split_lines(text: str) -> List[str]:
    return [line for line in text.splitlines() if line.strip()]


def _clean_line(line: str) -> str:
    cleaned = normalize_text(line)
    cleaned = cleaned.replace("—", "-").replace("–", "-")
    cleaned = re.sub(r"^\s*[\-\*\u2022]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", cleaned)
    if " - " not in cleaned and "," in cleaned:
        cleaned = cleaned.replace(",", " - ", 1)
    cleaned = re.sub(r"\s*-\s*", " - ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def clean_tracks(lines: Iterable[str]) -> List[str]:
    cleaned: List[str] = []
    for line in lines:
        item = _clean_line(line)
        if item:
            cleaned.append(item)
    return cleaned


def get_default_count() -> int:
    raw = os.getenv("AI_TRACK_COUNT", "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 200))
    return 50


def get_secret(name: str) -> str:
    if name in st.secrets:
        return str(st.secrets[name]).strip()
    value = os.getenv(name, "").strip()
    if not value:
        st.error(f"Falta la variable {name} en Secrets o en el entorno.")
        st.stop()
    return value


def build_custom_playlist_name(label: str) -> str:
    date_str = datetime.now().strftime("%d/%m/%y")
    trimmed = " ".join(label.strip().split())[:8]
    if trimmed:
        return f"IAList {trimmed} {date_str}"
    return f"IAList {date_str}"


def clear_query_params() -> None:
    try:
        st.query_params.clear()
    except Exception:
        try:
            st.experimental_set_query_params()
        except Exception:
            pass


class NoCacheHandler(CacheHandler):
    def get_cached_token(self):
        return None

    def save_token_to_cache(self, token_info):
        return None


def get_spotify_client() -> spotipy.Spotify:
    client_id = get_secret("SPOTIFY_CLIENT_ID")
    client_secret = get_secret("SPOTIFY_CLIENT_SECRET")
    redirect_uri = get_secret("SPOTIFY_REDIRECT_URI")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        cache_handler=NoCacheHandler(),
        show_dialog=True,
        open_browser=False,
    )

    token_info = st.session_state.get("token_info")
    code = st.query_params.get("code")

    if code:
        try:
            token_info = auth_manager.get_access_token(code, as_dict=True)
        except Exception as exc:
            st.error(f"No se pudo completar el login con Spotify: {exc}")
            st.stop()
        st.session_state["token_info"] = token_info
        clear_query_params()
        st.rerun()

    if not token_info:
        auth_url = auth_manager.get_authorize_url()
        st.info("Necesitas iniciar sesion con Spotify para continuar.")
        st.link_button("Login con Spotify", auth_url)
        st.stop()

    if auth_manager.is_token_expired(token_info):
        try:
            token_info = auth_manager.refresh_access_token(token_info["refresh_token"])
        except Exception as exc:
            st.error(f"No se pudo refrescar el token: {exc}")
            st.session_state.pop("token_info", None)
            st.stop()
        st.session_state["token_info"] = token_info

    return spotipy.Spotify(auth=token_info["access_token"])


with st.sidebar:
    st.subheader("Modo de generacion")
    if st.button("Cambiar usuario de Spotify"):
        st.session_state.pop("token_info", None)
        clear_query_params()
        st.rerun()
    source = st.radio(
        "Como quieres crear la lista?",
        [
            "Pegar lista",
            "Cargar archivo",
        ],
        key="source",
    )
    track_count = st.number_input(
        "Numero de canciones",
        min_value=1,
        max_value=200,
        value=get_default_count(),
        step=1,
        key="track_count",
    )
    playlist_label = st.text_input(
        "Nombre corto para la playlist (max 8)",
        max_chars=8,
        key="playlist_label",
        placeholder="temazos",
    )
    st.caption("Nombre final: IAList <tu texto> <fecha>. Ej: IAList temazos 30/03/26")
    st.caption("Si OpenAI falla, usamos songs.txt automaticamente.")


prompt = ""
raw_text = ""
uploaded_bytes = None

if source == "Prompt (OpenAI)":
    prompt = st.text_input(
        "Describe tu playlist",
        placeholder="Ej: synthwave para conducir de noche",
        key="prompt",
    )
    st.caption("Necesita saldo en OpenAI. Si falla, usamos songs.txt.")
elif source == "Pegar lista":
    raw_text = st.text_area(
        "Pega aqui tu lista (formato Artista - Cancion)",
        height=220,
        placeholder="1. Queen - Radio Ga Ga\n2. The Police - Every Breath You Take",
        key="raw_text",
    )
elif source == "Cargar archivo":
    uploaded = st.file_uploader(
        "Sube un archivo .txt o .csv con lineas 'Artista - Cancion'",
        type=["txt", "csv"],
        key="uploaded_file",
    )
    if uploaded:
        uploaded_bytes = uploaded.getvalue()


def build_queries() -> List[str]:
    if source == "Prompt (OpenAI)":
        if not prompt.strip():
            st.warning("Escribe un prompt para generar la lista.")
            return []
        try:
            return generate_tracks_with_openai(prompt, track_count)
        except Exception as exc:
            st.warning(f"OpenAI fallo: {exc}. Usando songs.txt.")
            return load_fallback_songs()
    if source == "Pegar lista":
        return clean_tracks(_split_lines(raw_text))
    if source == "Cargar archivo":
        if not uploaded_bytes:
            st.warning("Sube un archivo para continuar.")
            return []
        try:
            text = uploaded_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = uploaded_bytes.decode("latin-1")
        return clean_tracks(_split_lines(text))
    return load_fallback_songs()


def run_create_playlist() -> None:
    with st.spinner("Conectando con Spotify..."):
        try:
            spotify = get_spotify_client()
            user = spotify.current_user()
        except Exception as exc:
            st.error(f"No se pudo conectar con Spotify: {exc}")
            st.stop()
    st.info(f"Autenticado como: {user.get('display_name') or user.get('id')}")

    queries = build_queries()
    queries = dedupe_queries(queries)[:track_count]
    if not queries:
        st.error("No hay canciones validas para procesar.")
        st.stop()

    with st.expander(f"Lista final ({len(queries)} canciones)"):
        st.code("\n".join(queries))

    description = (
        f"Playlist generada por IA para: {prompt}"
        if source == "Prompt (OpenAI)"
        else "Playlist generada desde archivo/lista/fallback."
    )
    playlist_name = build_custom_playlist_name(playlist_label)

    with st.spinner("Creando playlist y agregando canciones..."):
        try:
            playlist = spotify.current_user_playlist_create(
                name=playlist_name,
                public=True,
                collaborative=False,
                description=description,
            )
        except Exception as exc:
            st.error(f"No se pudo crear la playlist: {exc}")
            st.stop()

        playlist_id = playlist.get("id")
        playlist_url = playlist.get("external_urls", {}).get("spotify", "")
        if not playlist_id:
            st.error("No se pudo obtener el ID de la playlist.")
            st.stop()

        try:
            results = add_tracks_from_queries(spotify, playlist_id, queries)
        except Exception as exc:
            st.error(f"No se pudieron agregar canciones: {exc}")
            st.stop()

    st.success("Playlist creada.")
    if playlist_url:
        st.markdown(f"[Abrir playlist en Spotify]({playlist_url})")

    st.subheader("Resumen")
    st.write(f"Usuario: {user.get('display_name') or user.get('id')}")
    st.write(f"Encontradas en Spotify: {len(results['found'])}")
    st.write(f"No encontradas: {len(results['not_found'])}")
    st.write(f"Anadidas: {len(results['added'])}")

    if results["not_found"]:
        with st.expander("Canciones no encontradas"):
            st.code("\n".join(results["not_found"]))


if st.button("Crear playlist en Spotify", type="primary"):
    st.session_state["pending_create"] = True

if st.session_state.get("pending_create"):
    run_create_playlist()
    st.session_state["pending_create"] = False

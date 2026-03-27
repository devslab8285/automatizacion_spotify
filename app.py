import os
import re
from typing import Iterable, List

import streamlit as st

from main import (
    add_tracks_from_queries,
    build_ai_playlist_name,
    build_client,
    build_fallback_playlist_name,
    dedupe_queries,
    generate_tracks_with_openai,
    load_fallback_songs,
    normalize_text,
)


st.set_page_config(page_title="Spotify AI Agent", page_icon="🎧", layout="centered")
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


with st.sidebar:
    st.subheader("Modo de generación")
    source = st.radio(
        "¿Cómo quieres crear la lista?",
        [
            "Prompt (OpenAI)",
            "Pegar lista",
            "Cargar archivo",
            "Usar songs.txt (fallback)",
        ],
    )
    track_count = st.number_input(
        "Número de canciones",
        min_value=1,
        max_value=200,
        value=get_default_count(),
        step=1,
    )
    st.caption("Si OpenAI falla, usamos songs.txt automáticamente.")


prompt = ""
raw_text = ""
uploaded_bytes = None

if source == "Prompt (OpenAI)":
    prompt = st.text_input(
        "Describe tu playlist",
        placeholder="Ej: synthwave para conducir de noche",
    )
    st.caption("Necesita saldo en OpenAI. Si falla, usamos songs.txt.")
elif source == "Pegar lista":
    raw_text = st.text_area(
        "Pega aquí tu lista (formato Artista - Canción)",
        height=220,
        placeholder="1. Queen - Radio Ga Ga\n2. The Police - Every Breath You Take",
    )
elif source == "Cargar archivo":
    uploaded = st.file_uploader(
        "Sube un archivo .txt o .csv con líneas 'Artista - Canción'",
        type=["txt", "csv"],
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
            st.warning(f"OpenAI falló: {exc}. Usando songs.txt.")
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


if st.button("Crear playlist en Spotify", type="primary"):
    with st.spinner("Conectando con Spotify..."):
        try:
            spotify = build_client()
            user = spotify.current_user()
        except Exception as exc:
            st.error(f"No se pudo conectar con Spotify: {exc}")
            st.stop()

    queries = build_queries()
    queries = dedupe_queries(queries)[:track_count]
    if not queries:
        st.error("No hay canciones válidas para procesar.")
        st.stop()

    with st.expander(f"Lista final ({len(queries)} canciones)"):
        st.code("\n".join(queries))

    description = (
        f"Playlist generada por IA para: {prompt}"
        if source == "Prompt (OpenAI)"
        else "Playlist generada desde archivo/lista/fallback."
    )
    playlist_name = (
        build_ai_playlist_name(prompt)
        if source == "Prompt (OpenAI)" and prompt.strip()
        else build_fallback_playlist_name()
    )

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
    st.write(f"Añadidas: {len(results['added'])}")

    if results["not_found"]:
        with st.expander("Canciones no encontradas"):
            st.code("\n".join(results["not_found"]))

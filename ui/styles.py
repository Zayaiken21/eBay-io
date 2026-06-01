from pathlib import Path
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent
STYLES_DIR = BASE_DIR / "styles"

def load_css(file_name: str = "styles.css") -> None:
    css_path = STYLES_DIR / file_name
    if not css_path.exists():
        st.error(f"CSS file not found: {css_path}")
        return

    css = css_path.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
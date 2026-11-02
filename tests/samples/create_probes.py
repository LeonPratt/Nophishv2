"""Render public synthetic evaluation fixtures with empty fields; no user captures."""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FONT = Path("C:/Windows/Fonts/arial.ttf")


def render(name, heading, lines, fields=(), button=None, navigation=None):
    image = Image.new("RGB", (1000, 700), "#f4f6f8")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((180, 90, 820, 620), radius=12, fill="white", outline="#c8ced6")
    draw.text((225, 130), heading, font=ImageFont.truetype(str(FONT), 32), fill="#182333")
    y = 200
    for line in lines:
        draw.text((225, y), line, font=ImageFont.truetype(str(FONT), 22), fill="#334155")
        y += 38
    for label in fields:
        draw.text((225, y), label, font=ImageFont.truetype(str(FONT), 22), fill="#334155")
        draw.rectangle((225, y + 30, 770, y + 75), outline="#94a3b8", width=2)
        y += 100
    if button:
        draw.rounded_rectangle((225, y + 10, 520, y + 65), radius=5, fill="#245ac4")
        draw.text((245, y + 22), button, font=ImageFont.truetype(str(FONT), 24), fill="white")
    if navigation:
        draw.text((850, 25), navigation, font=ImageFont.truetype(str(FONT), 22), fill="#245ac4")
    image.save(HERE / name)


if __name__ == "__main__":
    render("probe-unrelated.png", "Northwind Library", ["Sign in to your library account"],
           ["Email address", "Password"], "Sign in")
    render("probe-french.png", "Bibliotheque du quartier", ["Connexion a votre compte"],
           ["Adresse electronique", "Mot de passe"], "Se connecter")
    render("probe-passwordless.png", "Northwind Library", ["Access your account using a one-time code"],
           ["Email address"], "Continue")
    render("probe-article.png", "Community garden news", ["Our spring planting day is on Saturday.",
           "Bring gloves and a small watering can.", "Email the team to volunteer."])
    render("probe-login-link.png", "Community garden news", ["Our spring planting day is on Saturday.",
           "Bring gloves and a small watering can."], navigation="Log in")

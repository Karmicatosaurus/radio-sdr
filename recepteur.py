"""Configuration et récepteur rtl_fm"""

import json
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, NamedTuple, TypedDict, cast

import numpy as np
import numpy.typing as npt
from gi.repository import GLib


class Mode(NamedTuple):
    libelle: str
    arguments: list[str]  # arguments de démodulation pour rtl_fm
    taux_audio: int  # fréquence d'échantillonnage (Hz)


class Favori(TypedDict):
    frequence: float
    mode: int
    nom: str


class Config(TypedDict):
    frequence: float
    mode: int
    gain_auto: bool
    gain: float
    squelch: int
    ppm: int
    volume: float
    favoris: list[Favori]


def _mode(
    libelle: str,
    demodulation: str,
    taux: int,
    taux_sortie: int | None = None,
    options: tuple[str, ...] = (),
) -> Mode:
    arguments: list[str] = ["-M", demodulation, "-s", str(taux), *options]
    if taux_sortie is not None:
        arguments += ["-r", str(taux_sortie)]
    return Mode(libelle, arguments, taux_sortie or taux)


TAILLE_BLOC: Final[int] = 4096  # octets lus à chaque itération
FICHIER_CONFIG: Final[Path] = (
    Path(GLib.get_user_config_dir()) / "radio-sdr" / "config.json"
)

MODES: Final[tuple[Mode, ...]] = (
    _mode("FM large (radio FM)", "wbfm", 200000, taux_sortie=48000),
    _mode("FM étroite (NFM)", "fm", 16000),
    _mode("AM", "am", 12000, options=("-E", "dc")),
    _mode("BLU supérieure (USB)", "usb", 12000),
    _mode("BLU inférieure (LSB)", "lsb", 12000),
)

VALEURS_PAR_DEFAUT: Final[Config] = {
    "frequence": 100.0,
    "mode": 0,
    "gain_auto": True,
    "gain": 30.0,
    "squelch": 0,
    "ppm": 0,
    "volume": 0.7,
    "favoris": [],
}

MODELE_FAVORI: Final[Favori] = {"frequence": 0.0, "mode": 0, "nom": ""}

# Mots repérés dans la sortie d'erreur de rtl_fm pour choisir le message affiché
MOTS_CLES_ERREUR: Final[tuple[str, ...]] = (
    "fail",
    "error",
    "no supported",
    "usb_claim",
    "busy",
    "not found",
    "aborting",
)


def _ecrire_fichier(fichier: Path, texte: str, description: str) -> None:
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        fichier.write_text(texte)
    except OSError as erreur:
        print(f"Impossible d'enregistrer {description} : {erreur}", file=sys.stderr)


def _valeur_valide(valeur: object, modele: object) -> object | None:
    if (
        isinstance(modele, float)
        and not isinstance(valeur, bool)
        and isinstance(valeur, int | float)
    ):
        return float(valeur)
    return valeur if type(valeur) is type(modele) else None


def _mode_valide(mode: object) -> bool:
    return isinstance(mode, int) and 0 <= mode < len(MODES)


def _valeurs_valides(
    donnees: Mapping[str, object], modele: Mapping[str, object]
) -> dict[str, object | None]:
    return {
        cle: _valeur_valide(donnees.get(cle), attendu)
        for cle, attendu in modele.items()
    }


def _valider_favori(donnees: object) -> Favori | None:
    if not isinstance(donnees, dict):
        return
    favori: dict[str, object | None] = _valeurs_valides(donnees, MODELE_FAVORI)
    if None in favori.values() or not _mode_valide(favori["mode"]):
        return
    return cast(Favori, favori)


def valider_config(donnees: dict[str, object]) -> Config:
    defauts: Mapping[str, object] = VALEURS_PAR_DEFAUT
    config: dict[str, object] = {
        cle: defauts[cle] if valeur is None else valeur
        for cle, valeur in _valeurs_valides(donnees, defauts).items()
    }
    if not _mode_valide(config["mode"]):
        config["mode"] = VALEURS_PAR_DEFAUT["mode"]
    favoris: object = config["favoris"]
    assert isinstance(favoris, list)
    config["favoris"] = [
        favori for favori in map(_valider_favori, favoris) if favori is not None
    ]
    return cast(Config, config)


def charger_config() -> Config:
    try:
        texte: str = FICHIER_CONFIG.read_text()
        donnees: object = json.loads(texte)
    except (OSError, ValueError):
        return valider_config({})
    if not isinstance(donnees, dict):
        return valider_config({})
    config: Config = valider_config(donnees)
    return config


def enregistrer_config(config: Config) -> None:
    _ecrire_fichier(
        FICHIER_CONFIG,
        json.dumps(config, indent=2, ensure_ascii=False),
        "la configuration",
    )


# Récepteur
def commande_rtl_fm(config: Config) -> list[str]:
    commande: list[str] = [
        "rtl_fm",
        "-f",
        str(round(config["frequence"] * 1e6)),
        *MODES[config["mode"]].arguments,
        "-p",
        str(config["ppm"]),
    ]
    if not config["gain_auto"]:
        commande += ["-g", f"{config['gain']:.1f}"]
    if config["squelch"] > 0:
        commande += ["-l", str(config["squelch"])]
    return [*commande, "-"]


def commande_pacat(taux_audio: int) -> list[str]:
    return [
        "pacat",
        "--playback",
        "--raw",
        "--format=s16le",
        f"--rate={taux_audio}",
        "--channels=1",
        "--latency-msec=100",
        "--client-name=Radio SDR",
        "--stream-name=Écoute",
    ]


def choisir_message_erreur(lignes: list[str]) -> str:
    return next(
        (
            ligne
            for ligne in reversed(lignes)
            if any(mot in ligne.lower() for mot in MOTS_CLES_ERREUR)
        ),
        lignes[-1] if lignes else "rtl_fm s'est arrêté",
    )


class Recepteur:
    def __init__(
        self, sur_niveau: Callable[[float], bool], sur_erreur: Callable[[str], bool]
    ) -> None:
        self.sur_niveau: Callable[[float], bool] = sur_niveau
        self.sur_erreur: Callable[[str], bool] = sur_erreur
        self.volume: float = 0.7
        self.rtl: subprocess.Popen[bytes] | None = None
        self.sortie: subprocess.Popen[bytes] | None = None
        self.fil: threading.Thread | None = None
        self.fil_erreurs: threading.Thread | None = None
        self.en_marche: bool = False

    def _processus(self) -> tuple[subprocess.Popen[bytes], ...]:
        return tuple(
            processus for processus in (self.rtl, self.sortie) if processus is not None
        )

    def demarrer(self, config: Config) -> bool:
        self.arreter()
        try:
            self.rtl = subprocess.Popen(
                commande_rtl_fm(config),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            self.sortie = subprocess.Popen(
                commande_pacat(MODES[config["mode"]].taux_audio),
                stdin=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as erreur:
            self.arreter()
            self.sur_erreur(f"Programme introuvable : {erreur.filename!s}")
            return False

        self.en_marche = True
        self.fil = threading.Thread(target=self._pomper, daemon=True)
        self.fil.start()
        self.fil_erreurs = threading.Thread(
            target=self._surveiller_stderr, args=(self.rtl,), daemon=True
        )
        self.fil_erreurs.start()
        return True

    def _pomper(self) -> None:
        rtl, sortie = self.rtl, self.sortie
        assert rtl is not None and rtl.stdout is not None
        assert sortie is not None and sortie.stdin is not None
        compteur: int = 0
        while self.en_marche:
            donnees: bytes = rtl.stdout.read(TAILLE_BLOC)
            if not donnees:
                break
            if len(donnees) % 2:
                donnees = donnees[:-1]
            echantillons: npt.NDArray[np.float32] = np.frombuffer(
                donnees, dtype=np.int16
            ).astype(np.float32)
            echantillons *= self.volume
            np.clip(echantillons, -32768, 32767, out=echantillons)
            try:
                sortie.stdin.write(echantillons.astype(np.int16).tobytes())
            except (BrokenPipeError, ValueError, OSError):
                break
            compteur += 1
            if compteur % 4 == 0:
                efficace: float = (
                    float(np.sqrt(np.mean(np.square(echantillons)))) / 32768.0
                )
                GLib.idle_add(self.sur_niveau, min(1.0, efficace * 3.0))
        GLib.idle_add(self.sur_niveau, 0.0)

    def _surveiller_stderr(self, processus: subprocess.Popen[bytes]) -> None:
        assert processus.stderr is not None
        lignes: list[str] = []
        for brut in processus.stderr:
            ligne: str = brut.decode(errors="replace").strip()
            if ligne:
                lignes = [*lignes[-14:], ligne]
        code_retour: int = processus.wait()
        if self.en_marche and processus is self.rtl:
            if code_retour < 0:
                lignes.append(f"rtl_fm a planté (signal {-code_retour})")
            GLib.idle_add(self.sur_erreur, choisir_message_erreur(lignes))

    def arreter(self) -> None:
        self.en_marche = False
        for processus in self._processus():
            if processus.poll() is None:
                processus.terminate()
        for processus in self._processus():
            try:
                processus.wait(timeout=2)
            except subprocess.TimeoutExpired:
                processus.kill()
        for fil in (self.fil, self.fil_erreurs):
            if fil and fil is not threading.current_thread():
                fil.join(timeout=2)
        for processus in self._processus():
            for flux in (processus.stdin, processus.stdout, processus.stderr):
                if flux:
                    flux.close()
        self.rtl = self.sortie = self.fil = self.fil_erreurs = None

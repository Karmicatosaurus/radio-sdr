"""Comportement de la fenêtre GTK4 / libadwaita de Radio SDR."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Final, NamedTuple, TypeVarTuple

from gi.repository import Adw, Gdk, GLib, Gtk

from recepteur import (
    MODES,
    Config,
    Favori,
    Recepteur,
    charger_config,
    enregistrer_config,
)

DOSSIER: Final[Path] = Path(__file__).resolve().parent
FICHIER_CSS: Final[Path] = DOSSIER / "style.css"
FICHIER_BLP: Final[Path] = DOSSIER / "fenetre.blp"
DELAI_RELANCE_MS: Final[int] = 400

ONGLET_RADIO: Final[str] = "radio"
ONGLET_FAVORIS: Final[str] = "favoris"

DonneesRappel = TypeVarTuple("DonneesRappel")


class EtatLecture(NamedTuple):
    texte: str
    icone: str
    libelle: str
    style: str


ETATS_LECTURE: Final[dict[bool, EtatLecture]] = {
    True: EtatLecture(
        "À l'écoute", "media-playback-stop-symbolic", "Arrêter", "destructive-action"
    ),
    False: EtatLecture(
        "Arrêté", "media-playback-start-symbolic", "Écouter", "suggested-action"
    ),
}


def charger_css() -> None:
    affichage: Gdk.Display | None = Gdk.Display.get_default()
    if affichage is None:
        return
    fournisseur = Gtk.CssProvider()
    fournisseur.load_from_path(str(FICHIER_CSS))
    Gtk.StyleContext.add_provider_for_display(
        affichage, fournisseur, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def formater_frequence(frequence: float) -> str:
    return f"{frequence:.4f} MHz".replace(".", ",", 1)


def bouton_icone(
    icone: str,
    info_bulle: str,
    rappel: Callable[[Gtk.Button, *DonneesRappel], None],
    *donnees: *DonneesRappel,
) -> Gtk.Button:
    bouton = Gtk.Button(
        icon_name=icone,
        tooltip_text=info_bulle,
        valign=Gtk.Align.CENTER,
        css_classes=["flat"],
    )
    bouton.connect("clicked", rappel, *donnees)
    return bouton


def compiler_blueprint(fichier: Path) -> str:
    """Compile un fichier Blueprint (.blp) en XML GtkBuilder."""
    compilateur: str | None = shutil.which("blueprint-compiler")
    if compilateur is None:
        raise RuntimeError(
            "blueprint-compiler est introuvable : installez le paquet "
            "« blueprint-compiler » pour charger l'interface."
        )

    """resultat = subprocess.run(
        [compilateur, "compile", str(fichier)],
        capture_output=True,
        text=True,
    )"""

    resultat = subprocess.run(
        [compilateur, "compile", str(fichier)],
        check=False,
        capture_output=True,
        text=True,
    )
    if resultat.returncode != 0:
        raise RuntimeError(
            f"Échec de la compilation de {fichier.name} :\n"
            f"{resultat.stderr or resultat.stdout}"
        )
    return resultat.stdout


@Gtk.Template(string=compiler_blueprint(FICHIER_BLP))
class FenetreRadio(Adw.ApplicationWindow):
    __gtype_name__ = "FenetreRadio"

    # Widgets décrits dans fenetre.blp (attribut = id Blueprint)
    notifications: Adw.ToastOverlay = Gtk.Template.Child()
    onglets: Adw.ViewStack = Gtk.Template.Child()
    etiquette_freq: Gtk.Label = Gtk.Template.Child()
    etiquette_etat: Gtk.Label = Gtk.Template.Child()
    vumetre: Gtk.LevelBar = Gtk.Template.Child()
    bouton_lecture: Gtk.Button = Gtk.Template.Child()
    contenu_lecture: Adw.ButtonContent = Gtk.Template.Child()
    ligne_freq: Adw.SpinRow = Gtk.Template.Child()
    ligne_mode: Adw.ComboRow = Gtk.Template.Child()
    ligne_gain_auto: Adw.SwitchRow = Gtk.Template.Child()
    ligne_gain: Adw.SpinRow = Gtk.Template.Child()
    ligne_squelch: Adw.SpinRow = Gtk.Template.Child()
    ligne_ppm: Adw.SpinRow = Gtk.Template.Child()
    curseur_volume: Gtk.Scale = Gtk.Template.Child()
    groupe_favoris: Adw.PreferencesGroup = Gtk.Template.Child()

    en_ecoute: bool = False
    _id_relance: int = 0

    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application)
        self.config: Config = charger_config()
        self.recepteur: Recepteur = Recepteur(self._sur_niveau, self._sur_erreur)
        self.ligne_favoris: list[Adw.PreferencesRow] = []

        config = self.config
        self.ligne_mode.set_model(Gtk.StringList.new([mode.libelle for mode in MODES]))
        self.ligne_mode.set_selected(config["mode"])
        self.ligne_freq.set_value(config["frequence"])
        self.ligne_gain_auto.set_active(config["gain_auto"])
        self.ligne_gain.set_value(config["gain"])
        self.ligne_squelch.set_value(config["squelch"])
        self.ligne_ppm.set_value(config["ppm"])
        self.curseur_volume.set_value(config["volume"])
        self.recepteur.volume = config["volume"]

        self._reconstruire_favoris()
        self._actualiser_ui()

    def _creer_ligne_favori(self, favori: Favori) -> Adw.EntryRow:
        ligne = Adw.EntryRow(
            title=f"{formater_frequence(favori['frequence'])} . {MODES[favori['mode']].libelle}",
            text=favori["nom"],
            show_apply_button=True,
        )
        ligne.connect("apply", self._sur_renommage_favori, favori)
        ligne.add_suffix(
            bouton_icone(
                "media-playback-start-symbolic",
                "Écouter",
                self._sur_accord_favori,
                favori,
            )
        )
        ligne.add_suffix(
            bouton_icone(
                "user-trash-symbolic", "Supprimer", self._sur_suppression_favori, favori
            )
        )
        return ligne

    def _reconstruire_favoris(self) -> None:
        for ligne in self.ligne_favoris:
            self.groupe_favoris.remove(ligne)
        self.ligne_favoris = [
            self._creer_ligne_favori(favori) for favori in self.config["favoris"]
        ] or [
            Adw.ActionRow(
                title="Aucun favori",
                subtitle="Utilisez + pour enregistrer la fréquence actuelle",
            )
        ]
        for ligne in self.ligne_favoris:
            self.groupe_favoris.add(ligne)

    def _lire_reglages(self) -> None:
        self.config["frequence"] = round(self.ligne_freq.get_value(), 4)
        self.config["mode"] = self.ligne_mode.get_selected()
        self.config["gain_auto"] = self.ligne_gain_auto.get_active()
        self.config["gain"] = self.ligne_gain.get_value()
        self.config["squelch"] = int(self.ligne_squelch.get_value())
        self.config["ppm"] = int(self.ligne_ppm.get_value())
        self.config["volume"] = self.curseur_volume.get_value()

    def _actualiser_ui(self) -> None:
        mode: int = self.ligne_mode.get_selected()
        if mode >= len(MODES):
            return
        etat: EtatLecture = ETATS_LECTURE[self.en_ecoute]
        self.etiquette_freq.set_label(formater_frequence(self.ligne_freq.get_value()))
        self.etiquette_etat.set_label(f"{etat.texte} - {MODES[mode].libelle}")
        self.contenu_lecture.set_icon_name(etat.icone)
        self.contenu_lecture.set_label(etat.libelle)
        for autre in ETATS_LECTURE.values():
            self.bouton_lecture.remove_css_class(autre.style)
        self.bouton_lecture.add_css_class(etat.style)

    def _demarrer(self) -> None:
        self._lire_reglages()
        self.en_ecoute = self.recepteur.demarrer(self.config)
        self._actualiser_ui()

    def _arreter(self) -> None:
        self.recepteur.arreter()
        self.en_ecoute = False
        self.vumetre.set_value(0)
        self._actualiser_ui()

    def _annuler_relance(self) -> None:
        if self._id_relance:
            GLib.source_remove(self._id_relance)
            self._id_relance = 0

    def _favoris_modifies(self) -> None:
        enregistrer_config(self.config)
        self._reconstruire_favoris()

    # Rappels déclarés dans fenetre.blp
    @Gtk.Template.Callback()
    def _sur_clic_lecture(self, _bouton: Gtk.Button) -> None:
        if self.en_ecoute:
            self._arreter()
        else:
            self._demarrer()

    @Gtk.Template.Callback()
    def _sur_volume(self, curseur: Gtk.Scale) -> None:
        self.recepteur.volume = curseur.get_value()

    @Gtk.Template.Callback()
    def _sur_ajout_favori(self, _bouton: Gtk.Button) -> None:
        frequence: float = round(self.ligne_freq.get_value(), 4)
        mode: int = self.ligne_mode.get_selected()
        favoris: list[Favori] = self.config["favoris"]
        if any(
            favori["frequence"] == frequence and favori["mode"] == mode
            for favori in favoris
        ):
            return
        favoris.append(
            {"frequence": frequence, "mode": mode, "nom": f"{frequence:g} MHz"}
        )
        favoris.sort(key=lambda favori: favori["frequence"])
        self._favoris_modifies()

    @Gtk.Template.Callback()
    def _sur_frequence_modifie(self, *_args: object) -> None:
        self._sur_reglage_modifie()

    @Gtk.Template.Callback()
    def _sur_reglage_modifie(self, *_args: object) -> None:
        self._actualiser_ui()
        if not self.en_ecoute:
            return
        # Regroupe les changements rapides (molette, flèches) en une seule relance
        self._annuler_relance()
        self._id_relance = GLib.timeout_add(DELAI_RELANCE_MS, self._relance_differee)

    @Gtk.Template.Callback()
    def _sur_fermeture(self, *args: object) -> bool:
        self._annuler_relance()
        self.recepteur.arreter()
        self._lire_reglages()
        enregistrer_config(self.config)
        return False

    # Autres rappels
    def _relance_differee(self) -> bool:
        self._id_relance = 0
        if self.en_ecoute:
            self._demarrer()
        return GLib.SOURCE_REMOVE

    def _sur_niveau(self, valeur: float) -> bool:
        self.vumetre.set_value(valeur if self.en_ecoute else 0)
        return GLib.SOURCE_REMOVE

    def _sur_erreur(self, message: str) -> bool:
        if self.en_ecoute:
            self._arreter()
        self.notifications.add_toast(Adw.Toast(title=f"Erreur : {message}", timeout=6))
        return GLib.SOURCE_REMOVE

    def _sur_renommage_favori(self, ligne: Adw.EntryRow, favori: Favori) -> None:
        favori["nom"] = ligne.get_text()
        enregistrer_config(self.config)

    def _sur_accord_favori(self, _bouton: Gtk.Button, favori: Favori) -> None:
        self.ligne_mode.set_selected(favori["mode"])
        self.ligne_freq.set_value(favori["frequence"])
        if not self.en_ecoute:
            self._demarrer()
        self.onglets.set_visible_child_name(ONGLET_RADIO)

    def _sur_suppression_favori(self, _bouton: Gtk.Button, favori: Favori) -> None:
        self.config["favoris"].remove(favori)
        self._favoris_modifies()

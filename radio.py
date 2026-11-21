#!/usr/bin/env python3
"""Petite radio SDR (RTL2832U) en Python + GTK4/libadwaita.

Le démodulateur est `rtl_fm` ; l'audio est envoyé vers PipeWire/PulseAudio
via `pacat`. Le volume et le vumètre sont gérés en Python (numpy)."""

import sys
from typing import Final, override

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw

from fenetre import FenetreRadio, charger_css

ID_APPLICATION: Final[str] = "fr.karmicatosaurus.RadioSDR"


class ApplicationRadio(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=ID_APPLICATION)

    @override
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        charger_css()

    @override
    def do_activate(self) -> None:
        fenetre = self.get_active_window() or FenetreRadio(self)
        fenetre.present()


if __name__ == "__main__":
    sys.exit(ApplicationRadio().run(sys.argv))

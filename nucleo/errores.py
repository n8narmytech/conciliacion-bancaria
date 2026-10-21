"""Errores que se muestran tal cual a quien concilia."""


class ErrorDeValidacion(Exception):
    """
    Problema con los archivos que se subieron, no con el sistema.

    El mensaje está escrito para quien está conciliando y se muestra tal
    cual en la pantalla, sin el detalle técnico que acompaña a los errores
    inesperados.
    """

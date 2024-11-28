"""Custom log formatters.

This module defines custom log formatters class. Its content is designed to be
referenced by logconf.ini alone.
"""

import logging
import colorama



class ColorLogFormatter(logging.Formatter):
    """Log formatter that adds color to the debug level word."""

    namecolors = {
        'DEBUG': colorama.Fore.BLUE,
        'INFO': colorama.Fore.GREEN,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Style.DIM + colorama.Fore.RED,
        'CRITICAL': colorama.Fore.RED
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        colorama.init()

    def _colorname(self, name):
        s = self.namecolors.get(name, "")
        return colorama.Style.BRIGHT + s + name + colorama.Style.RESET_ALL

    def format(self, record):
        """Set records's attribute levelnamecolor and call superclass's format
        method.

        Attribute record.levelnamecolor hold the name of the log level plus any
        terminal code to make it colored. It can be used in the format given in
        logconf.ini.
        """

        record.levelnamecolor = self._colorname(record.levelname)
        return super().format(record)

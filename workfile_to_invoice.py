#!/usr/bin/env python3

import argparse
import datetime
import locale
import logging
import logging.config
import os
import subprocess
import shutil
import sys

import invoice
import workfile



SELFPATH = os.path.dirname(os.path.realpath(sys.argv[0]))



class SectionNameError(ValueError):
    """Raised when the given section is not found in the workfile."""



def logging_getHandler(name):
    """Get the logging handler with the given name."""

    for h in logging.getLogger().handlers:
        if h.name == name:
            return h
    return None



def find_section(wf, title):
    date_start = datetime.date.today() - datetime.timedelta(days=91)
    date_end = datetime.date.today() + datetime.timedelta(days=30)
    wff = wf.filter(date_start, date_end, title)

    if len(wff.sections) == 0:
        # Approximate match not supported yet
        raise SectionNameError(f"No section titled {title!r} between {date_start} and {date_end}")

    if len(wff.sections) > 1:
        logging.warning("%d sections with name %r have been found. Using the last one.",
                        len(wff.sections), title)

    return wff.sections[-1].section



def update_invoice(inv, sec):
    newitems = []
    for e in sec.full_entries:
        newitems.append(invoice.InvoiceItem(sec.title, e.date, e.hours, "heures", e.rate, 0))

    inv.items = newitems
    inv.invdate = datetime.date.today()
    return inv



def update_invoice_file(invoice_file, sec, show_diff=False, write=False, force=False):
    try:
        inv = invoice.Invoice.fromfile(invoice_file)
    except FileNotFoundError:
        logging.error("Creating non-existing invoice file not supported yet")
        raise

    update_invoice(inv, sec)

    new_invoice_file = invoice_file + ".new"
    with open(new_invoice_file, "w") as fp:
        fp.write(str(inv))

    if show_diff:
        subprocess.call(["diff", "--color", "--text", "--unified", invoice_file, new_invoice_file])

    if write and not force:
        res = input("Write these changes? [yN] ")
        if not res or res not in "yY":
            logging.info("Not writing the changes. New version still accessible in: %s",
                         new_invoice_file)
            write = False

    if write:
        bak_invoice_file = invoice_file + ".bak"
        logging.info("Writing changes to %s, old workfile copied to %s",
                     invoice_file, bak_invoice_file)
        shutil.move(invoice_file, bak_invoice_file)
        shutil.move(new_invoice_file, invoice_file)



def main():
    locale.setlocale(locale.LC_ALL, '')
    logging.config.fileConfig(os.path.join(SELFPATH, "logconf.ini"),
                              disable_existing_loggers=False)

    parser = argparse.ArgumentParser(description="Génère ou met à jour les "
                                     "factures à partir d'un Workfile")
    parser.add_argument("--workfile", "-w",
                        help="Fichier Workfile à utiliser")
    parser.add_argument("--section-title", "-s",
                        help="Section dont générer ou mettre à jour la facture")
    parser.add_argument("--invoice-dir", "-i",
                        help="Répertoire contenant les fichiers LaTeX des factures")
    parser.add_argument("--invoice-file", "-f",
                        help="Fichier LaTeX à mettre à jour")
    parser.add_argument("--show-diff", "-d", action="store_true",
                        help="Afficher les différences prêtes à être appliquées")
    parser.add_argument("--write", action="store_true",
                        help="Écrase les factures avec la nouvelle version")
    parser.add_argument("--force", action="store_true",
                        help="Avec --write, écrit le fichier sans demander de confirmation")
    parser.add_argument("--verbose", "-v", action="count", default=0,
                        help="Augmente le niveau de verbosité")
    parser.add_argument("--quiet", "-q", action="count", default=0,
                        help="Diminue le niveau de verbosité")

    args = parser.parse_args()

    workfilename = args.workfile
    section_title = args.section_title
    invoice_dir = args.invoice_dir
    invoice_file = args.invoice_file
    show_diff = args.show_diff
    write = args.write
    force = args.force
    verbose = args.verbose - args.quiet

    loglevels = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"]
    ch = logging_getHandler("consoleHandler")
    curlevel = logging.getLevelName(ch.level)
    curlevel = loglevels.index(curlevel)
    verbose = min(len(loglevels) - 1, max(0, curlevel + verbose))
    ch.setLevel(loglevels[verbose])

    if not (show_diff or write):
        logging.warning("No --show-diff or --write specified. Nothing to do, exiting now.")
        return 0

    if workfilename is None:
        logging.error("No workfile given")
        return 1

    if section_title is None:
        logging.error("Automatic section - invoice matching not supported yet. Use --section-title")
        return 1

    if invoice_dir is None and invoice_file is None:
        logging.error("No invoice dir or invoice file given")
        return 1

    if invoice_file is None:
        logging.error("Automatic section - invoice matching not supported yet. Use --invoice-file")
        return 1

    if force and not write:
        logging.info("--force used without --write is ignored")

    if write and not force and not show_diff:
        logging.debug("--write will ask for confirmation, enabling --show-diff")
        show_diff = True

    wf = workfile.Workfile.fromfile(workfilename)
    sec = find_section(wf, section_title)
    update_invoice_file(invoice_file, sec, show_diff, write, force)

    return 0



if __name__ == "__main__":
    sys.exit(main())

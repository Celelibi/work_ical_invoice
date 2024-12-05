#!/usr/bin/env python3

import argparse
import collections
import dataclasses
import datetime
import locale
import logging
import logging.config
import os
import subprocess
import shutil
import sys

import approxmatch
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



def filter_sections(wf, title=None):
    """Find the recent Workfile sections with the given title."""

    date_start = datetime.date.today() - datetime.timedelta(days=91)
    date_end = datetime.date.today() + datetime.timedelta(days=30)
    return wf.filter(date_start, date_end, title)



def list_titles_dates(wf, title=None):
    """List the section titles with the date range of the entries. If a title
    is given, show also the matching score."""

    sections = filter_sections(wf).sections
    if title is not None:
        scores = {s.title: approxmatch.approx_score(title, s.title) for s in sections}
        sections = sorted(sections, key=lambda s: scores[s.title])

    for sec in sections:
        s = sec.section
        if title is None:
            print(f"{s.first_date()} - {s.last_date()}: {s.title}")
        else:
            score = scores[s.title]
            print(f"{s.first_date()} - {s.last_date()}: score {score:2d}: {s.title}")



def find_section(wf, title):
    """Find the Workfile section with the given title."""

    wff = filter_sections(wf, title)
    if len(wff.sections) == 1:
        return wff.sections[0].section

    if len(wff.sections) > 1:
        logging.warning("%d sections with name %r have been found. Using the last one.",
                        len(wff.sections), title)
        return wff.sections[-1].section

    logging.info("No section with exact title %r", title)
    logging.info("Switching to approximate matching")

    wff = filter_sections(wf)
    titles = [s.title for s in wff.sections]
    actual_title = approxmatch.approx_match(title, titles)

    if approxmatch.approx_score(title, actual_title) / len(actual_title) < 0.1:
        logging.info("Matched with: %s", actual_title)
        wff = filter_sections(wf, actual_title)
        return wff.sections[-1].section

    logging.error("No good match found for title: %s", title)
    raise SectionNameError(f"No match found for title: {title!r}")



def partial_match_dataclass(ref, data, **include_fields):
    """Find the subset of `data' that are equal to `ref' when comparing only
    a subset of fields.

    By default, only fields defined with compare=True (the default) are
    compared.
    Additional keyword arguments allow to select which fields are compared.
    True values include the field name in the comparison, False values ignore
    them. If all values are True, the default is to exclude fields. If all the
    values are False, the default is to include the fields defined with
    compare=True.

    Except when forcing the inclusion of a field defined compare=False, mixing
    True and False keyword arguments is not allowed.
    """

    fields = set()
    for f in dataclasses.fields(ref):
        if f.compare or include_fields.pop(f.name, False):
            fields.add(f.name)

    if not any(include_fields.values()):
        fields -= include_fields.keys()
    elif all(include_fields.values()):
        fields &= include_fields.keys()
    else:
        raise NotImplementedError("Mixing True and False values not supported")

    return [d for d in data if all(getattr(ref, f) == getattr(d, f) for f in fields)]



def match_items(ref, items, **include_fields):
    """Match items using function partial_match_dataclass.
    """

    assert "desc" not in include_fields, "Items are always matched with an approximate description"
    matches = partial_match_dataclass(ref, items, desc=False, **include_fields)
    return [m for m in matches if approxmatch.approx_score(ref.desc, m.desc) / len(ref.desc) < 0.1]



def _update_invoice_ignore_sum_match(added, removed):
    """If an item already match a sum of existing items, match them. Allow
    approximate description."""

    for a in added.elements():
        matches = match_items(a, removed.elements(), time=False)
        total_time = sum(m.time for m in matches)

        if total_time == a.time:
            logging.debug("Ignoring a sum-match for item: %s", a)
            for m in matches:
                logging.debug("Partial-match: %s", m)
                removed[m] -= 1
            added[a] -= 1

    return added, removed



def _update_invoice_fix_partial(added, removed, curitems):
    """If existing items match everything but are missing some time, either
    fix the item or add one. Allow approximate description."""

    for a in added.elements():
        matches = match_items(a, removed.elements(), time=False)
        total_time = sum(m.time for m in matches)

        if len(matches) == 1:
            m = matches[0]
            logging.debug("Fixing in-place partial-match for item: %s", a)
            logging.debug("Partial-match: %s", m)
            removed[m] -= 1
            added[a] -= 1

            curitems[m] -= 1
            newitem = invoice.Item(m.desc, m.date, a.time, m.unit, m.rate, m.vat)
            curitems[newitem] += 1

        elif len(matches) > 1 and total_time < a.time:
            logging.debug("Found partial matches that don't add up to the amount "
                          "of hours for: %s", a)
            for m in matches:
                logging.debug("Partial-match: %s", m)
                removed[m] -= 1
            added[a] -= 1
            remtime = a.time - total_time
            logging.info("Adding new item with remaining hours: %f", remtime)

            # Apply immediately to avoid further matching.
            newitem = invoice.Item(matches[0].desc, a.date, remtime, a.unit, a.rate, a.vat)
            curitems[newitem] += 1

        elif len(matches) > 1 and total_time > a.time:
            # Too many hours? Let it go to a remove and add
            logging.debug("Too many hours (%s) for: %s", total_time, a)
            for m in matches:
                logging.debug("Item counting toward the overtime: %s", m)

    return added, removed



def update_invoice(inv, sec):
    """Update an Invoice object to have all the items related to the Workfile section."""

    newitems = collections.Counter()
    for e in sec.full_entries:
        item = invoice.Item(sec.title, e.date, e.hours, "heures", e.rate, 0)
        newitems[item] += 1

    curitems = collections.Counter(inv.items)

    added = newitems - curitems
    removed = curitems - newitems

    for item in newitems & curitems:
        logging.debug("Ignoring a match: %s", item)

    added, removed = _update_invoice_ignore_sum_match(added, removed)
    added, removed = _update_invoice_fix_partial(added, removed, curitems)

    if added or removed:
        inv.items = sorted((curitems - removed + added).elements())
        inv.invdate = datetime.date.today()

    return inv



def update_invoice_file(invoice_file, sec, show_diff=False, write=False, force=False, template=None):
    """Update an invoice file according to the entries in a given Workfile section."""

    if template is not None:
        inv = invoice.Invoice.fromfile(template)
    else:
        inv = invoice.Invoice.fromfile(invoice_file)

    update_invoice(inv, sec)

    new_invoice_file = invoice_file + ".new"
    with open(new_invoice_file, "w") as fp:
        fp.write(str(inv))

    if show_diff:
        subprocess.call(["diff", "--color", "--new-file", "--text", "--unified",
                         invoice_file, new_invoice_file])

    if not write:
        os.unlink(new_invoice_file)

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
        try:
            shutil.move(invoice_file, bak_invoice_file)
        except FileNotFoundError as e:
            if e.filename != invoice_file:
                raise
        shutil.move(new_invoice_file, invoice_file)



def main():
    locale.setlocale(locale.LC_ALL, '')
    logging.config.fileConfig(os.path.join(SELFPATH, "logconf.ini"),
                              disable_existing_loggers=False)

    parser = argparse.ArgumentParser(description="Génère ou met à jour les "
                                     "factures à partir d'un Workfile")
    parser.add_argument("--workfile", "-w",
                        help="Fichier Workfile à utiliser")
    parser.add_argument("--list-sections", "-l", action="store_true",
                        help="Liste les sections récentes du Workfile disponibles pour --section-name")
    parser.add_argument("--section-title", "-s",
                        help="Section dont générer ou mettre à jour la facture")
    parser.add_argument("--invoice-dir", "-i",
                        help="Répertoire contenant les fichiers LaTeX des factures")
    parser.add_argument("--invoice-file", "-f",
                        help="Fichier LaTeX à mettre à jour ou à écrire")
    parser.add_argument("--template", "-t",
                        help="Fichier LaTeX à mettre à jour à copier")
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
    list_sections = args.list_sections
    section_title = args.section_title
    invoice_dir = args.invoice_dir
    invoice_file = args.invoice_file
    template = args.template
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

    if not (show_diff or write or list_sections):
        logging.warning("No --show-diff or --write or --list-sections specified. " \
                        "Nothing to do, exiting now.")
        return 0

    if workfilename is None:
        logging.error("No workfile given")
        return 1

    if (show_diff or write) and section_title is None:
        logging.error("Automatic section - invoice matching not supported yet. Use --section-title")
        return 1

    if (show_diff or write) and invoice_dir is None and invoice_file is None:
        logging.error("No invoice dir or invoice file given")
        return 1

    if (show_diff or write) and invoice_file is None:
        logging.error("Automatic section - invoice matching not supported yet. Use --invoice-file")
        return 1

    if force and not write:
        logging.info("--force used without --write is ignored")

    if write and not force and not show_diff:
        logging.debug("--write will ask for confirmation, enabling --show-diff")
        show_diff = True

    wf = workfile.Workfile.fromfile(workfilename)

    if list_sections:
        list_titles_dates(wf, section_title)

        if not (show_diff or write):
            return 0

    sec = find_section(wf, section_title)
    update_invoice_file(invoice_file, sec, show_diff, write, force, template)

    return 0



if __name__ == "__main__":
    sys.exit(main())

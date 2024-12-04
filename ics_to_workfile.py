#!/usr/bin/env python3

import argparse
import collections
import datetime
import decimal
import locale
import logging
import logging.config
import os
import subprocess
import shutil
import sys

import icalendar

import approxmatch
import workfile



SELFPATH = os.path.dirname(os.path.realpath(sys.argv[0]))



def logging_getHandler(name):
    """Get the logging handler with the given name."""

    for h in logging.getLogger().handlers:
        if h.name == name:
            return h
    return None



def dedup(objs, eqfunc=None, hashfunc=None):
    """Remove duplicate objects as with list(set(objs)) but with custom
    equality and / or hash functions.

    If given, the comparison is based on the equality function. If not, native
    == operator is used.
    If given, the hash function is used to make the objects hashable. If not
    given, the builtin hash function is used on the objects.
    """

    class Wrapper:
        """Wrapper to allow to hash and test equality of any object."""

        def __init__(self, obj):
            self.obj = obj

        def __eq__(self, other):
            if eqfunc is not None:
                return eqfunc(self.obj, other.obj)
            return self.obj == other.obj

        def __hash__(self):
            if hashfunc is not None:
                return hashfunc(self.obj)
            return hash(self.obj)

    return [w.obj for w in set(Wrapper(obj) for obj in objs)]



def partition(it, keyfunc):
    """Partition an iterable according to a key function.

    Return a dict with one entry per key value as returned by keyfunc.
    """

    retval = collections.defaultdict(list)
    for e in it:
        retval[keyfunc(e)].append(e)
    return retval



def sorted_dict(d, sortkey=None):
    """Return an OrderedDict with keys sorted.

    If sortkey is given, use it as argument to the function 'sorted'."""

    retval = collections.OrderedDict()
    for k in sorted(d, key=sortkey):
        retval[k] = d[k]
    return retval



def sum_events_duration(events):
    """Sum the events duration from a structure returned by icalendar."""

    return sum(decimal.Decimal((e["DTEND"].dt - e["DTSTART"].dt).seconds) / 60 / 60 for e in events)



def structure_by_date(cal):
    """Take an icalendar Calendar and return the events partitionned by SUMMARY and DESCRIPTION."""

    events = cal.walk("VEVENT")
    events = dedup(events, hashfunc=lambda e: hash(e.to_ical()))
    events.sort(key=lambda e: e["DTSTART"].dt)

    bycourse = partition(events, keyfunc=lambda e: (e["SUMMARY"], e["DESCRIPTION"]))
    bycourse = sorted_dict(bycourse, sortkey=lambda l: bycourse[l][-1]["DTSTART"].dt)

    bycoursedate = collections.OrderedDict()
    for (course, students), events in bycourse.items():
        bydate = partition(events, keyfunc=lambda e: e["DTSTART"].dt.date())
        bycoursedate[course, students] = sorted_dict(bydate)

    return bycoursedate



def ics_to_workfile(ics, rate):
    """Build a Workfile from an ics file.

    The hourly rate of the created Workfile has to be given as argument since
    it's not contained in the ics file.
    """

    with open(ics) as fp:
        cal = icalendar.Calendar.from_ical(fp.read())

    wf = workfile.Workfile([])

    bycoursedate = structure_by_date(cal)
    for (course, students), bydate in bycoursedate.items():
        sectitle_comment = f" {course}"
        if students:
            sectitle_comment += " avec " + students.removeprefix("Groupe d'étudiants : ")

        sec = workfile.WorkfileSection([workfile.WorkfileEntryComment(sectitle_comment)])
        for date, evs in bydate.items():
            total_duration = sum_events_duration(evs).normalize()
            entry = workfile.WorkfileEntryFull(date, total_duration, rate)
            sec.entries.append(entry)
        wf.sections.append(sec)

    return wf



def partial_entry_matches(entry, entries):
    """Finds partial matches between one workfile entry and a list of entries.

    It returns 3 lists of entries:
        - The list of entries that match only the date.
        - The list of entries that match the date and number of hours.
        - The list of entries that match the date and rate.
    """

    datematch = []
    datehoursmatch = []
    dateratematch = []
    for removed_entry in entries.elements():
        if removed_entry.date == entry.date:
            if removed_entry.hours != entry.hours and removed_entry.rate != entry.rate:
                datematch.append(removed_entry)
            if removed_entry.hours == entry.hours:
                datehoursmatch.append(removed_entry)
            if removed_entry.rate == entry.rate:
                dateratematch.append(removed_entry)

    return datematch, datehoursmatch, dateratematch



def _update_course_ignore_sum_match(added_entries, removed_entries):
    """If an entry already match a sum of existing entries, match them."""

    for added_entry in added_entries.elements():
        _, _, dateratematch = partial_entry_matches(added_entry, removed_entries)
        dateratematch_hours = sum(e.hours for e in dateratematch)

        if dateratematch_hours == added_entry.hours and len(dateratematch) > 1:
            logging.debug("Ignoring a sum-match for new entry: %s", added_entry)
            for e in dateratematch:
                logging.debug("Partial-match: %s", e)
                removed_entries[e] -= 1
            added_entries[added_entry] -= 1

    return added_entries, removed_entries



def _update_course_fix_partial(added_entries, removed_entries, wfsec):
    """If there are entries matching the date and rate, fix the entry if there's
    only one or add a new one if there are already several."""

    for added_entry in added_entries.elements():
        _, _, dateratematch = partial_entry_matches(added_entry, removed_entries)
        dateratematch_hours = sum(e.hours for e in dateratematch)

        if len(dateratematch) == 1:
            logging.debug("Fixing in-place a partial (date, rate) match for: %s", added_entry)
            logging.warning("Partial-match: %s", dateratematch[0])

            removed_entries[dateratematch[0]] -= 1
            added_entries[added_entry] -= 1

            # Modify the existing entry in order to keep the comment / formatting / ordering.
            idx = wfsec.entries.index(dateratematch[0])
            wfsec.entries[idx].hours = added_entry.hours

        elif len(dateratematch) > 1 and dateratematch_hours < added_entry.hours:
            logging.debug("Found partial matches that don't add up to the amount "
                          "of hours for: %s", added_entry)
            for e in dateratematch:
                logging.debug("Partial-match: %s", e)
                removed_entries[e] -= 1
            remhours = added_entry.hours - dateratematch_hours
            logging.info("Adding new entry with remaining hours: %f", remhours)

            # Apply immediately to avoid further matching.
            added_entries[added_entry] -= 1
            added_entry.hours = remhours
            wfsec.entries.append(added_entry)

        elif len(dateratematch) > 1 and dateratematch_hours > added_entry.hours:
            # Too many hours? Let it go to a remove and add
            logging.debug("Too many hours (%s) for: %s", dateratematch_hours, added_entry)
            for e in dateratematch:
                logging.debug("Entry counting toward the overtime: %s", e)

    return added_entries, removed_entries



def _update_course_ignore_rate_nonmatch(added_entries, removed_entries):
    """Louldly ignore partial matches that don't match the rate. This is common
    since the hourly rate isn't in the ics file."""

    for added_entry in added_entries.elements():
        _, datehoursmatch, _ = partial_entry_matches(added_entry, removed_entries)

        if len(datehoursmatch) > 0:
            logging.warning("Hourly rate doesn't match for: %s", added_entry)
            for e in datehoursmatch:
                logging.warning("Non-match: %s", e)
                removed_entries[e] -= 1
            logging.warning("Not fixing anything")
            added_entries[added_entry] -= 1

    return added_entries, removed_entries



def _update_course_warn_date_only_match(added_entries, removed_entries):
    """Warn about date-only match, but perform them anyway."""

    for added_entry in added_entries.elements():
        datematch, _, _ = partial_entry_matches(added_entry, removed_entries)

        if len(datematch) > 0:
            logging.warning("Found matches for date but hours and rate don't match: %s",
                            added_entry)
            for e in datematch:
                logging.warning("Non-match: %s", e)
            logging.warning("Replacing with entry: %s", added_entry)

    return added_entries, removed_entries



def _update_course_apply_changes(wfsec, added_entries, removed_entries):
    """Add and remove entries to a Workfile section."""

    # Add new entries
    for added_entry in added_entries.elements():
        wfsec.entries.append(added_entry)

    # Find and discard removed entries
    for removed_entry in removed_entries.elements():
        wfsec.entries.remove(removed_entry)

    return wfsec



def update_course(wf, newsec, icsstart, icsend):
    """Update the workfile wf in the interval icsstart - icsend according to newsec.

    It updates only the section of wf that has the same title as newsec. (Or close enough.)
    The section to update is searched within +/- 3 months of the date interval.
    Only entries exactly within the date interval are considered.
    Entries of wf that are not in newsec are discarded.
    Entries of wf that are missing compared to newsec are added.
    """

    logging.debug("Updating workfile for section : %s", newsec.title)

    sec_search_start = icsstart - datetime.timedelta(days=92)
    sec_search_end = icsend + datetime.timedelta(days=92)
    wff = wf.filter(sec_search_start, sec_search_end, newsec.title)

    if len(wff.sections) == 0:
        logging.info("No section found for: %s", newsec.title)
        logging.info("Doing an approximate search")

        wff_notitle = wf.filter(sec_search_start, sec_search_end)
        titles = [s.title for s in wff_notitle.sections]
        actual_title = approxmatch.approx_match(newsec.title, titles)

        if approxmatch.approx_score(newsec.title, actual_title) / len(actual_title) < 0.1:
            logging.info("Matched with: %s", actual_title)
            wff = wf.filter(sec_search_start, sec_search_end, actual_title)

    if len(wff.sections) == 0:
        logging.info("No section found for: %s", newsec.title)
        logging.info("Adding it")
        wf.sections.append(newsec)
        return

    if len(wff.sections) > 1:
        logging.error("Several sections in the workfile match the date interval: "
                      "%s to %s with the name %s", sec_search_start, sec_search_end, newsec.title)
        logging.error("Not doing anything about it!")
        return

    wffsec = wff.sections[0].filter(icsstart, icsend)
    wfsec = wffsec.section

    newsec_entries = collections.Counter(newsec.full_entries)
    current_entries = collections.Counter(wffsec.full_entries)

    added_entries = newsec_entries - current_entries
    removed_entries = current_entries - newsec_entries

    for e in newsec_entries & current_entries:
        logging.debug("Ignoring a match: %s", e)

    # Keep filtering the added entires to remove non-exact matches
    added_entries, removed_entries = _update_course_ignore_sum_match(added_entries, removed_entries)
    added_entries, removed_entries = _update_course_fix_partial(added_entries, removed_entries, wfsec)
    added_entries, removed_entries = _update_course_ignore_rate_nonmatch(added_entries, removed_entries)
    added_entries, removed_entries = _update_course_warn_date_only_match(added_entries, removed_entries)

    wfsec = _update_course_apply_changes(wfsec, added_entries, removed_entries)

    if not added_entries and not removed_entries:
        logging.debug("Not trying to sort an untouched section")
        return

    try:
        wfsec.sort()
    except workfile.UnsortableError as e:
        logging.debug(e.args[0])
    else:
        logging.debug("Sorted section %s", wfsec.title)




def main():
    locale.setlocale(locale.LC_ALL, '')
    logging.config.fileConfig(os.path.join(SELFPATH, "logconf.ini"),
                              disable_existing_loggers=False)

    parser = argparse.ArgumentParser(description="Met à jour un Workfile à partir d'un ICS")
    parser.add_argument("ics", help="Fichier ICS")
    parser.add_argument("--rate", "-r", type=decimal.Decimal,
                        help="TJM supposé")
    parser.add_argument("--workfile", "-w",
                        help="Fichier Workfile à mettre à jour")
    parser.add_argument("--print-ics", "-p", action="store_true",
                        help="Afficher tout le contenu du ficher ICS")
    parser.add_argument("--show-diff", "-d", action="store_true",
                        help="Afficher les différences prêtes à être appliquées")
    parser.add_argument("--write", action="store_true",
                        help="Écrase le workfile avec la nouvelle version")
    parser.add_argument("--force", "-f", action="store_true",
                        help="Avec --write, écrit le fichier sans demander de confirmation")
    parser.add_argument("--verbose", "-v", action="count", default=0,
                        help="Augmente le niveau de verbosité")
    parser.add_argument("--quiet", "-q", action="count", default=0,
                        help="Diminue le niveau de verbosité")

    args = parser.parse_args()

    icsfilename = args.ics
    rate = args.rate
    workfilename = args.workfile
    print_ics = args.print_ics
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

    if workfilename is None and (show_diff or write):
        logging.critical("No workfile specified")
        return 1

    if force and not write:
        logging.info("--force used without --write is ignored")

    if write and not force and not show_diff:
        logging.debug("--write will ask for confirmation, enabling --show-diff")
        show_diff = True

    if not (print_ics or show_diff or write):
        logging.warning("No --print-ics or --show-diff or --write specified. "
                        "Nothing to do, exiting now.")
        return 0

    logging.info("Reading ics file: %s", icsfilename)
    icswf = ics_to_workfile(icsfilename, rate)

    if print_ics:
        print(icswf)

    if workfilename is None:
        logging.debug("No workfile specified, exiting")
        return 0

    # Plannings are sent by full weeks, sometimes more than one at a time.
    icsstart = icswf.first_date()
    icsend = icswf.last_date()
    icsstart -= datetime.timedelta(days=icsstart.weekday())
    icsend += datetime.timedelta(days=7 - icsend.weekday())

    logging.info("Reading workfile %s", workfilename)
    wf = workfile.Workfile.fromfile(workfilename)
    for sec in icswf.sections:
        update_course(wf, sec, icsstart, icsend)

    newworkfile = workfilename + ".new"
    with open(newworkfile, "w") as fp:
        print(wf, file=fp)
        print("", file=fp)

    if show_diff:
        subprocess.call(["diff", "--color", "--text", "--unified", "--show-function-line=^#",
                         workfilename, newworkfile])

    if write and not force:
        res = input("Write these changes? [yN] ")
        if not res or res not in "yY":
            logging.info("Not writing the changes. New version still accessible in: %s",
                         newworkfile)
            write = False

    if write:
        bakworkfile = workfilename + ".bak"
        logging.info("Writing changes to %s, old workfile copied to %s",
                     workfilename, bakworkfile)
        shutil.move(workfilename, bakworkfile)
        shutil.move(newworkfile, workfilename)

    return 0



if __name__ == "__main__":
    sys.exit(main())

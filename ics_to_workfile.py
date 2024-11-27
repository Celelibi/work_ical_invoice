#!/usr/bin/env python3

import argparse
import collections
import dataclasses
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
import more_itertools



SELFPATH = os.path.dirname(os.path.realpath(sys.argv[0]))



class UnsortableError(ValueError):
    pass



@dataclasses.dataclass
class WorkfileEntry:
    pass



@dataclasses.dataclass(order=True, unsafe_hash=True)
class WorkfileEntryFull(WorkfileEntry):
    date: datetime.date
    hours: decimal.Decimal
    rate: decimal.Decimal
    comment: str = dataclasses.field(default=None, compare=False)
    prespaces: int = dataclasses.field(default=1, compare=False)

    def __str__(self):
        s = f"{self.date} {self.hours} {self.rate}"
        if self.comment is not None:
            s += " " * self.prespaces
            s += f"#{self.comment}"
        return s



@dataclasses.dataclass
class WorkfileEntryComment(WorkfileEntry):
    comment: str

    def __str__(self):
        return "#" + "\n#".join(self.comment.split("\n"))



@dataclasses.dataclass
class WorkfileSection:
    entries: list

    def _title_comment_count(self):
        for i, e in enumerate(self.entries):
            if not isinstance(e, WorkfileEntryComment):
                return i

        return len(self.entries)

    @property
    def title_comment(self):
        n = self._title_comment_count()

        if n == 0:
            return None

        if n == 1:
            return self.entries[0]

        return WorkfileEntryComment("\n".join(c.comment for c in self.entries[:n]))

    @property
    def title(self):
        c = self.title_comment
        if c is None:
            return None
        return c.comment

    @property
    def full_entries(self):
        return (e for e in self.entries if isinstance(e, WorkfileEntryFull))

    def first_date(self):
        return min((e.date for e in self.full_entries), default=None)

    def last_date(self):
        return max((e.date for e in self.full_entries), default=None)

    def sort(self):
        n = self._title_comment_count()
        if not all(isinstance(e, WorkfileEntryFull) for e in self.entries[n:]):
            raise UnsortableError("Can't sort sections with comments")
        self.entries[n:] = sorted(self.entries[n:])

    def __str__(self):
        return "\n".join(str(e) for e in self.entries)



@dataclasses.dataclass
class Workfile:
    sections: list

    def first_date(self):
        return min((e.date for s in self.sections for e in s.full_entries), default=None)

    def last_date(self):
        return max((e.date for s in self.sections for e in s.full_entries), default=None)

    def filter(self, start, end, title=None):
        return WorkfileFiltered(self, start, end, title)

    def __str__(self):
        return "\n\n".join(str(s) for s in self.sections)



class WorkfileSectionFiltered:
    def __init__(self, sec, start, end):
        assert isinstance(sec, WorkfileSection)

        self.section = sec
        self.start_date = start
        self.end_date = end

    @property
    def title_comment(self):
        return self.section.title_comment

    @property
    def title(self):
        return self.section.title

    @property
    def full_entries(self):
        ret = []
        for e in self.section.full_entries:
            if e.date < self.end_date and e.date >= self.start_date:
                ret.append(e)
        return ret

    def filter(self, start, end):
        start = max(start, self.start_date)
        end = min(end, self.end_date)
        return WorkfileSectionFiltered(self.section, start, end)

    def __str__(self):
        prependtitle_comment = [self.title_comment]
        if prependtitle_comment[0] is None:
            prependtitle_comment = []
        return "\n".join(str(e) for e in prependtitle_comment + self.full_entries)



class WorkfileFiltered:
    def __init__(self, wf, start, end, title=None):
        self.workfile = wf
        self.start_date = start
        self.end_date = end
        self.title = title

    @property
    def sections(self):
        ret = []
        for s in self.workfile.sections:
            sec_first = s.first_date()
            sec_last = s.last_date()

            if sec_first is None or sec_last is None:
                continue

            if self.title is not None and s.title != self.title:
                continue

            if sec_first < self.end_date and sec_last >= self.start_date:
                ret.append(WorkfileSectionFiltered(s, self.start_date, self.end_date))
        return ret

    def __str__(self):
        return "\n\n".join(str(s) for s in self.sections)



def logging_getHandler(name):
    for h in logging.getLogger().handlers:
        if h.name == name:
            return h
    return None



def dedup(objs, eqfunc=None, hashfunc=None):
    class Wrapper:
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
    retval = collections.defaultdict(list)
    for e in it:
        retval[keyfunc(e)].append(e)
    return retval



def sorted_dict(d, sortkey=None):
    retval = collections.OrderedDict()
    for k in sorted(d, key=sortkey):
        retval[k] = d[k]
    return retval



def sum_events_duration(events):
    return sum(decimal.Decimal((e["DTEND"].dt - e["DTSTART"].dt).seconds) / 60 / 60 for e in events)



def structure_by_date(cal):
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



def ics_to_workfile(ics):
    with open(ics) as fp:
        cal = icalendar.Calendar.from_ical(fp.read())

    wf = Workfile([])

    bycoursedate = structure_by_date(cal)
    for (course, students), bydate in bycoursedate.items():
        prefix = "Groupe d'étudiants : "
        if students:
            if students.startswith(prefix):
                students = students[len(prefix):]
            sectitle_comment = f" {course} avec {students}"
        else:
            sectitle_comment = f" {course}"

        sec = WorkfileSection([WorkfileEntryComment(sectitle_comment)])
        for date, evs in bydate.items():
            total_duration = sum_events_duration(evs).normalize()
            entry = WorkfileEntryFull(date, total_duration, 80)
            sec.entries.append(entry)
        wf.sections.append(sec)

    return wf



def read_workfile_section(fp):
    fp = more_itertools.peekable(fp)

    entries = []
    for line in fp:
        if line.endswith("\n"):
            line = line[:-1]

        if line == "":
            break

        if line.startswith("#"):
            entries.append(WorkfileEntryComment(line[1:]))
            continue

        date, hours, rate, *linecomm = line.split(" ", maxsplit=3)
        date = datetime.date.fromisoformat(date)
        hours = decimal.Decimal(hours)
        rate = decimal.Decimal(rate)
        if linecomm:
            linecomm = linecomm[0]
            assert "#" in linecomm
            commidx = linecomm.index("#")
            prespaces = linecomm.count(" ", 0, commidx) + 1
            linecomm = linecomm[commidx + 1:]
        else:
            prespaces = None
            linecomm = None

        entries.append(WorkfileEntryFull(date, hours, rate, linecomm, prespaces))

    if not entries:
        raise StopIteration

    return WorkfileSection(entries)



def read_workfile(workfile):
    wf = Workfile([])
    state = "workfile"

    with open(workfile) as fp:
        while True:
            try:
                wf.sections.append(read_workfile_section(fp))
            except StopIteration:
                break

    return wf



def partial_entry_matches(entry, entries):
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



def update_course(wf, newsec, icsstart, icsend):
    logging.debug("Updating workfile for section :%s", newsec.title)

    sec_search_start = icsstart - datetime.timedelta(days=92)
    sec_search_end = icsend + datetime.timedelta(days=92)
    wff = wf.filter(sec_search_start, sec_search_end, newsec.title)

    if len(wff.sections) > 1:
        logging.error("Several sections in the workfile match the date interval: %s to %s with the name%s", sec_search_start, sec_search_end, newsec.title)
        logging.error("Not doing anything about it!")
        return

    if len(wff.sections) == 0:
        logging.info("No section found for:%s", newsec.title)
        logging.info("Adding it")
        wf.sections.append(newsec)
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

    # If an entry already match a sum of existing entries, match them
    for added_entry in added_entries.elements():
        _, _, dateratematch = partial_entry_matches(added_entry, removed_entries)
        dateratematch_hours = sum(e.hours for e in dateratematch)

        if dateratematch_hours == added_entry.hours and len(dateratematch) > 1:
            logging.debug("Ignoring a sum-match for new entry: %s", added_entry)
            for e in dateratematch:
                logging.debug("Partial-match: %s", e)
                removed_entries[e] -= 1
            added_entries[added_entry] -= 1

    # If there are entries matching the date and rate, fix the entry if there's
    # only one or add a new one if there are already several.
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
            logging.debug("Found partial matches that don't add up to the amount of hours for: %s", added_entry)
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

    # Louldly ignore partial matches that don't match the rate. This is common
    # since the hourly rate isn't in the ics file.
    for added_entry in added_entries.elements():
        _, datehoursmatch, _ = partial_entry_matches(added_entry, removed_entries)

        if len(datehoursmatch) > 0:
            logging.warning("Hourly rate doesn't match for: %s", added_entry)
            for e in datehoursmatch:
                logging.warning("Non-match: %s", e)
                removed_entries[e] -= 1
            logging.warning("Not fixing anything")
            added_entries[added_entry] -= 1

    # Warn about date-only match, but perform them anyway.
    for added_entry in added_entries.elements():
        datematch, _, _ = partial_entry_matches(added_entry, removed_entries)

        if len(datematch) > 0:
            logging.warning("Found matches for date but hours and rate don't match: %s", added_entry)
            for e in datematch:
                logging.warning("Non-match: %s", e)
            logging.warning("Replacing with entry: %s", added_entry)

    # Add new entries
    for added_entry in added_entries.elements():
        wfsec.entries.append(added_entry)

    # Find and discard removed entries
    for removed_entry in removed_entries.elements():
        wfsec.entries.remove(removed_entry)

    if not added_entries and not removed_entries:
        logging.debug("Not trying to sort an untouched section")
        return

    try:
        wfsec.sort()
    except UnsortableError as e:
        logging.debug(e.args[0])
    else:
        logging.debug("Sorted section%s", wfsec.title)




def main():
    locale.setlocale(locale.LC_ALL, '')
    logging.config.fileConfig(os.path.join(SELFPATH, "logconf.ini"), disable_existing_loggers=False)

    parser = argparse.ArgumentParser(description="Met à jour un Workfile à partir d'un ICS")
    parser.add_argument("ics", help="Fichier ICS")
    parser.add_argument("--workfile", "-w", help="Fichier Workfile à mettre à jour")
    parser.add_argument("--print-ics", "-p", action="store_true", help="Afficher tout le contenu du ficher ICS")
    parser.add_argument("--show-diff", "-d", action="store_true", help="Afficher les différences prêtes à être appliquées")
    parser.add_argument("--write", action="store_true", help="Écrase le workfile avec la nouvelle version")
    parser.add_argument("--force", "-f", action="store_true", help="Avec --write, écrit le fichier sans demander de confirmation")
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Augmente le niveau de verbosité")
    parser.add_argument("--quiet", "-q", action="count", default=0, help="Diminue le niveau de verbosité")

    args = parser.parse_args()

    icsfilename = args.ics
    workfile = args.workfile
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

    if workfile is None:
        logging.critical("No workfile specified")
        return 1

    if force and not write:
        logging.info("--force used without --write is ignored")

    if write and not force and not show_diff:
        logging.debug("--write will ask for confirmation, enabling --show-diff")
        show_diff = True

    logging.info("Reading ics file: %s", icsfilename)
    icswf = ics_to_workfile(icsfilename)

    if print_ics:
        print(icswf)

    # Plannings are sent by full weeks, sometimes more than one at a time.
    icsstart = icswf.first_date()
    icsend = icswf.last_date()
    icsstart -= datetime.timedelta(days=icsstart.weekday())
    icsend += datetime.timedelta(days=7 - icsend.weekday())

    logging.info("Reading workfile %s", workfile)
    wf = read_workfile(workfile)
    for sec in icswf.sections:
        update_course(wf, sec, icsstart, icsend)

    newworkfile = workfile + ".new"
    with open(newworkfile, "w") as fp:
        print(wf, file=fp)
        print("", file=fp)

    if show_diff:
        subprocess.call(["diff", "--color", "--text", "--unified", "--show-function-line=^#", workfile, newworkfile])

    if write and not force:
        res = input("Write these changes? [yN] ")
        if not res or res not in "yY":
            logging.info("Not writing the changes. New version still accessible in: %s", newworkfile)
            write = False

    if write:
        bakworkfile = workfile + ".bak"
        logging.info("Writing changes to %s, old workfile copied to %s", workfile, bakworkfile)
        shutil.move(workfile, bakworkfile)
        shutil.move(newworkfile, workfile)



if __name__ == "__main__":
    sys.exit(main())

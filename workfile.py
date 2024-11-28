"""Set of classes and functions to represent and manipulate a workfile."""

import dataclasses
import datetime
import decimal

import more_itertools



class UnsortableError(ValueError):
    """Raised when asking to sort the entries of a section containing comment
    entries."""



@dataclasses.dataclass
class WorkfileEntry:
    """Base class for the workfile entries."""



@dataclasses.dataclass(order=True, unsafe_hash=True)
class WorkfileEntryFull(WorkfileEntry):
    """Full entry of a workfile.

    It contains the following fields:
        - date: The date the work happened at
        - hours: The number of hours worked
        - rate: How many euro per hour will be paid for it
        - comment: The one-line comment string at the end of the line. Or None.
        - prespaces: The number of spaces before the comment. Irrelevant if comment is None.
    """

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
    """An workfile entry consisting of a single comment and nothing else."""

    comment: str

    def __str__(self):
        return "#" + "\n#".join(self.comment.split("\n"))



@dataclasses.dataclass
class WorkfileSection:
    """A workfile section.

    A section is a set of lines in the workfile separated by blank lines.
    If the first few lines of a section are comments, they are considered the
    title of the section. The titles can be used to identify the sections.
    """

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
    """A workfile is basically a list of sections."""

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
    """Section of a date-filtered workfile.

    Sections can be filtered by date so that only entries between two dates are
    returned.
    """

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
    """A Workfile filtered by date and optionally by title.

    The filter is based on the date and section titles.
    The sections accessible are filtered by date. Only sections containing
    non-comment entries are accessible.
    If a filter title is given, only the sections with the given title are
    accessible. If not given (or None), no title restriction is placed.
    """
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



def _read_workfile_section(fp):
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



def read_workfile(workfilename):
    wf = Workfile([])
    state = "workfile"

    with open(workfilename) as fp:
        while True:
            try:
                wf.sections.append(_read_workfile_section(fp))
            except StopIteration:
                break

    return wf

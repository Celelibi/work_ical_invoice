"""Set of classes and functions to represent and manipulate invoices."""

import dataclasses
import datetime
import decimal
import logging
import os
import re
import typing



class ParseError(RuntimeError):
    """Base class for parsing errors."""

class InvoiceFilenameError(ParseError):
    """Raised when the filename is invalid."""

class InvoiceContentError(ParseError):
    """Base class for errors about the content of the invoice."""

class InvoiceDateError(InvoiceContentError):
    """Raised when there is no date in the invoice."""

class InvoiceTextError(InvoiceContentError):
    """Raised when the invoice items are malformed (e.g: lack a date)"""



@dataclasses.dataclass(order=True, frozen=True)
class Item:
    """Represents an invoice item.

    The items fields are:
        - desc: text description of the item
        - date: the date related to the description (the date the course happened)
        - time: amount of time
        - unit: unit of the time
        - rate: price in euro per unit of time
    """
    desc: str
    date: datetime.datetime
    time: decimal.Decimal
    unit: str
    rate: decimal.Decimal
    vat: decimal.Decimal = 0



@dataclasses.dataclass
class Invoice:
    """An invoice is a list of items with other metadata.

    Invoice fields:
        - invnum: the id of the invoice
        - invdate: the date the invoice was produced
        - items: list of items
        - smallprints: text to add at the bottom of the invoice
    """

    # These values are hardcoded in the invoice module, and thus here too.
    default_smallprints: typing.ClassVar[str] = "Pas d'escompte pour règlement" \
        " anticipé.\n" \
        "En cas de retard de paiement, application de pénalités au taux Refi " \
        "appliqué par la BCE majoré de 10 points et indemnité forfaitaire " \
        "pour frais de recouvrement de 40 euros.\n" \
        "Dispensé d’immatriculation au registre du commerce et des sociétés " \
        "(RCS) et au répertoire des métiers (RM)."
    default_novatmsg: typing.ClassVar[str] = "TVA non applicable, art. 293 B du CGI"

    invnum: int
    invdate: datetime.date
    items: list[Item]
    smallprints: str
    template: str = None

    @classmethod
    def fromfile(cls, filename):
        """Create an invoice object from a filename."""

        if not filename.endswith(".tex"):
            logging.warning("Expecting a *.tex file. Got %r.", filename)

        logging.info("Reading invoice: %s", filename)
        basename = os.path.basename(filename)
        m = re.match(r'(\d+)_', basename)
        if not m:
            logging.critical("Filename %r does not start with an invoice number", basename)
            raise InvoiceFilenameError(f"Malformed filename {filename}")

        invnum = m.group(1)

        template = ""
        data = ""
        with open(filename) as fp:
            for line in fp:
                template += line
                commentpos = line.find("%")
                if commentpos == -1:
                    commentpos = None
                line = line[:commentpos]
                if line:
                    logging.debug("Read line: %r", line[:commentpos])
                    data += line[:commentpos]

        template = template.replace("{", "{{").replace("}", "}}")
        smallprints = cls.default_smallprints
        invdate = None
        items = []

        m = re.search(r'\\setvatno\{([^}]*)\}', data)
        if m:
            raise NotImplementedError("Having VAT number is not supported yet")
        smallprints += "\n" + cls.default_novatmsg

        m = re.search(r'\\setmoresmallprints\{([^}]*)\}', data)
        if m:
            smallprints += "\n" + m.group(1)
            template = re.sub(r"\\setmoresmallprints\{\{[^}]*\}\}", "{moresmallprints}", template)

        m = re.search(r'\\setinvoicedate\{([^}]*)\}', data)
        if m is None:
            logging.critical("No date detected in the invoice")
            raise InvoiceDateError("No invoice date")

        invdate = m.group(1)
        invdate = datetime.datetime.strptime(invdate, "%d/%m/%Y").date()
        template = re.sub(r"\\setinvoicedate\{\{[^}]*\}\}", "{invdate}", template)

        it = re.finditer(r'\\additem\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}', data)
        for m in it:
            text, time, unit, rate, vat = m.groups()
            m = re.match(r'(.*) - (\d{2}/\d{2}/\d{4})$', text)
            if m is None:
                raise InvoiceTextError(f"Invoice item {text!r} has invalid format")
            itemdesc = m.group(1)
            itemdate = m.group(2)
            itemdate = datetime.datetime.strptime(itemdate, "%d/%m/%Y").date()
            time = decimal.Decimal(time)
            rate = decimal.Decimal(rate)
            vat = decimal.Decimal(vat)
            items.append(Item(itemdesc, itemdate, time, unit, rate, vat))

        matches = re.finditer(r'\\additem\{\{([^}]*)\}\}\{\{([^}]*)\}\}\{\{([^}]*)\}\}\{\{([^}]*)\}\}\{\{([^}]*)\}\}', template)
        matches = list(matches)
        start = matches[0].start()
        end = matches[-1].end()
        template = template[:start] + "{items}" + template[end:]

        logging.debug("Read invoice %s: %s, %r, %r", invnum, invdate, smallprints, items)
        return cls(invnum, invdate, items, smallprints, template)

    def __str__(self):
        items = ""
        for i in self.items:
            date = i.date.strftime("%d/%m/%Y")
            items += f"\\additem{{{i.desc} - {date}}}{{{i.time}}}{{{i.unit}}}{{{i.rate}}}{{{i.vat}}}\n"

        items = items.strip()
        invdate = f"\\setinvoicedate{{{self.invdate.strftime('%d/%m/%Y')}}}"

        moresmallprints = self.smallprints
        if moresmallprints.startswith(self.default_smallprints):
            moresmallprints = moresmallprints[len(self.default_smallprints):].lstrip()

        if moresmallprints.startswith(self.default_novatmsg):
            moresmallprints = moresmallprints[len(self.default_novatmsg):].lstrip()

        moresmallprints = f"\\setmoresmallprints{{{moresmallprints}}}"

        return self.template.format(invdate=invdate, moresmallprints=moresmallprints, items=items)

# -*- coding: utf-8 -*-
from pyrevit import DB
import traceback
import logging

logger = logging.getLogger(__name__)


def normalize_string(text):
    """Safely normalize string values, always returning a unicode string.

    In IronPython 2, calling str() on a .NET System.String that contains
    non-ASCII characters (e.g. accented letters) produces a byte string
    encoded with the system default codec.  The pyRevit Routes JSON encoder
    then fails with 'unknown codec can't decode byte 0xNN'.

    By returning unicode we guarantee the JSON serialiser receives a proper
    text object regardless of the locale of the Revit model.
    """
    if text is None:
        return u"Unnamed"
    # Already a unicode string (normal case for .NET System.String in IronPython)
    if isinstance(text, unicode):
        return text.strip()
    # Byte string — decode with a permissive fallback
    if isinstance(text, str):
        try:
            return text.decode("utf-8").strip()
        except (UnicodeDecodeError, AttributeError):
            return text.decode("latin-1").strip()
    # Any other type (.NET object, int, etc.) — convert via unicode()
    try:
        return unicode(text).strip()
    except Exception:
        return u"Unnamed"


def repair_utf8(text):
    """Undo UTF-8 read as Latin-1 (u"N\xc3\xadvel" -> u"N\xedvel").

    IronPython 2's urllib.unquote turns each %XX of a UTF-8 path parameter into
    one character, so accented view names arrive garbled. Text that is not
    such a mis-decoding is returned unchanged.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeError, AttributeError):
        return text


def _category_key(text):
    return u"".join(normalize_string(text).lower().split())


def category_matches(requested, localized_name, builtin_name=None):
    """True when a requested category name matches a Revit category.

    Accepts the name in the model's language ("Paredes"), case-insensitive, or
    the English/API name ("Walls", "OST_Walls"), so the same request works in a
    Revit installed in any language.
    """
    wanted = _category_key(requested)
    if not wanted:
        return False
    if wanted == _category_key(localized_name):
        return True
    if not builtin_name:
        return False
    builtin = _category_key(builtin_name)
    if builtin.startswith(u"ost_"):
        builtin = builtin[4:]
    if wanted.startswith(u"ost_"):
        wanted = wanted[4:]
    # OST_GenericModel vs "Generic Models": also compare without a plural "s".
    return wanted in (builtin, builtin + u"s") or wanted.rstrip(u"s") == builtin


def find_category(doc, category_name):
    """Find a category by localized name, English/API name or OST_ name."""
    fallback = None
    for category in doc.Settings.Categories:
        if normalize_string(category.Name) == normalize_string(category_name):
            return category
        if fallback is None:
            builtin = None
            try:
                builtin = str(category.BuiltInCategory)
            except Exception:
                pass
            if category_matches(category_name, category.Name, builtin):
                fallback = category
    return fallback


def element_id_value(element_id):
    """Get the integer value from an ElementId.

    Revit 2025+ uses .Value (int64), older versions use .IntegerValue (int32).
    Revit 2026 removed .IntegerValue entirely.
    """
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


def element_id_from_int(value):
    """Build an ElementId from an integer.

    Revit 2024+ takes an Int64; Revit 2026 removed the Int32 constructor.
    Older versions only have the Int32 constructor.
    """
    try:
        from System import Int64
        return DB.ElementId(Int64(value))
    except Exception:
        return DB.ElementId(int(value))


def get_element_name(element):
    """
    Get the name of a Revit element.
    Useful for both FamilySymbol and other elements.
    """
    try:
        return element.Name
    except AttributeError:
        return DB.Element.Name.__get__(element)


def find_family_symbol_safely(doc, target_family_name, target_type_name=None):
    """
    Safely find a family symbol by name
    """
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)

        for symbol in collector:
            if symbol.Family.Name == target_family_name:
                if not target_type_name or get_element_name(symbol) == target_type_name:
                    return symbol
        return None
    except Exception as e:
        logger.error("Error finding family symbol: %s", str(e))
        return None

"""Unit conversions used across freshwater inflow components."""

CFS_TO_AFD = 1.983471
MGD_TO_AFD = 3.06888328
INCH_TO_FEET = 1.0 / 12.0


def cfs_to_afday(value):
    """Convert cubic feet per second to acre-feet per day."""
    return value * CFS_TO_AFD


def mgd_to_afday(value):
    """Convert million gallons per day to acre-feet per day."""
    return value * MGD_TO_AFD


def inches_to_acre_feet(inches, area_acres):
    """Convert inches over an area in acres to acre-feet."""
    return inches * INCH_TO_FEET * area_acres

#!/usr/bin/env python3

import argparse
import errno
import os
import stat
import struct
import sys
from pathlib import Path

import serial
from serial.tools import list_ports
from loguru import logger

if os.name == "posix":
    import fcntl
    import termios
else:
    fcntl = None
    termios = None

DEFAULT_BAUDRATE = 57600
DEFAULT_PORT = "/dev/ttyUSB0"


def setup_logger(verbosity: int) -> None:
    """
    Setup the logger.

    :param verbosity: The verbosity to use for logging.
    :type verbosity: int
    """
    # Remove the default logger to avoid duplicate logs
    logger.remove()
    logger.level("INFO", color="<green>")

    # Define logger format
    if verbosity >= 1:
        log_format = (
            "[<level>{level:}</level>]: "
            "<cyan>[{time:DD-MM-YYYY HH:mm:ss]}</cyan> | "
            "<green>[{name}</green>:<green>{function}</green>:<green>{line}]</green> - "
            "<level>{message}</level>"
        )
    else:
        log_format = "[<level>{level:}</level>]: <level>{message}</level>"

    # Add logger to write logs to stdout
    logger.add(sys.stdout, format=log_format, level="DEBUG", colorize=True)


def device_port_exists(port_path) -> bool:
    """
    Check if a given device port exists on Linux.

    :param port_path: The device port path (e.g., '/dev/ttyUSB0', 'ttyUSB0', '/dev/ttyACM0')
    :type port_path: str
    :return: True if the device port exists and is a character device, False otherwise
    :rtype: bool
    """

    logger.info("Checking device...")
    # Normalize the path - add /dev/ prefix if not present
    if not port_path.startswith("/dev/"):
        port_path = "/dev/" + port_path

    try:
        if not os.path.exists(port_path):
            return False

        # Get file status
        file_stat = os.stat(port_path)

        # Check if it's a character device
        return stat.S_ISCHR(file_stat.st_mode)

    except (OSError, IOError):
        # Handle permission errors or other OS-level issues
        return False


def read_bitstream_data(bitstream_file: str) -> bytearray:
    """Read the bitstream data from the specified file.

    :param bitstream_file: The bitstream file to be read.
    :type bitstream_file: str
    :return: The bitstream data read from the file.
    :rtype: bytearray
    """
    file = Path(bitstream_file)
    if not file.is_file():
        logger.error(
            f"File {bitstream_file} does not exist."
            + " Check for spelling and if a bitstream file was created."
        )
        raise FileNotFoundError

    with open(bitstream_file, "rb") as f:
        data = bytearray(f.read())

    return data


def parse_usb_vid_pid(usb_id: str) -> tuple[int, int]:
    """Parse a USB ID string in VID:PID format (hex)."""
    parts = usb_id.split(":", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid USB ID '{usb_id}'. Expected format is VID:PID (e.g. 0403:6001)."
        )

    try:
        vid = int(parts[0], 16)
        pid = int(parts[1], 16)
    except ValueError as e:
        raise ValueError(
            f"Invalid USB ID '{usb_id}'. VID and PID must be hexadecimal numbers."
        ) from e

    if not (0 <= vid <= 0xFFFF and 0 <= pid <= 0xFFFF):
        raise ValueError(
            f"Invalid USB ID '{usb_id}'. VID and PID must be 16-bit hexadecimal values."
        )

    return vid, pid


def resolve_port_by_usb_id(usb_id: str) -> str:
    """Resolve a serial device path by USB VID:PID."""
    vid, pid = parse_usb_vid_pid(usb_id)
    matching_ports = [
        port.device
        for port in list_ports.comports()
        if port.vid == vid and port.pid == pid and port.device
    ]

    if not matching_ports:
        raise ValueError(
            f"No serial device found for USB ID {vid:04x}:{pid:04x}."
        )

    if len(matching_ports) > 1:
        ports = ", ".join(matching_ports)
        raise ValueError(
            f"Multiple devices found for USB ID {vid:04x}:{pid:04x}: {ports}"
        )

    return matching_ports[0]


def resolve_target_port(port: str | None, usb_id: str | None) -> str:
    """Resolve final device port from CLI options."""
    if usb_id:
        try:
            return resolve_port_by_usb_id(usb_id)
        except ValueError as e:
            if port:
                logger.warning(
                    f"{e} Falling back to --port value '{port}'."
                )
                return port
            raise

    if port:
        return port

    return DEFAULT_PORT


def set_custom_baudrate(ser, baudrate: int) -> None:
    """Set a custom baud rate on Linux using termios.

    :param ser: The serial port object.
    :param baudrate: The custom baudrate to set.
    """
    if os.name != "posix" or fcntl is None or termios is None:
        raise RuntimeError(
            "Custom baud rate configuration is only supported on POSIX platforms."
        )

    # Linux termios2 ioctls and baud flags from asm-generic/termbits.h.
    # The layout below assumes struct termios2 is 44 bytes on the target platform.
    TCGETS2 = 0x802C542A
    TCSETS2 = 0x402C542B
    BOTHER = 0o010000
    CBAUD = 0o010017

    # Offsets in Linux termios2:
    # c_iflag(0), c_oflag(4), c_cflag(8), c_lflag(12),
    # c_line(16), c_cc[19](17-35), c_ispeed(36), c_ospeed(40)
    C_CFLAG_OFFSET = 8
    C_ISPEED_OFFSET = 36
    C_OSPEED_OFFSET = 40

    fd = ser.fileno()

    # Get current serial port settings
    buf = bytearray(fcntl.ioctl(fd, TCGETS2, b"\x00" * 44))

    # Set custom speed flag
    c_cflag = struct.unpack_from("I", buf, C_CFLAG_OFFSET)[0]
    c_cflag &= ~CBAUD
    c_cflag |= BOTHER

    # Pack and set new settings
    struct.pack_into("I", buf, C_CFLAG_OFFSET, c_cflag)
    struct.pack_into("I", buf, C_ISPEED_OFFSET, baudrate)
    struct.pack_into("I", buf, C_OSPEED_OFFSET, baudrate)
    fcntl.ioctl(fd, TCSETS2, bytes(buf))


def upload_bitstream(bitstream_file: str, baudrate: int, port: str) -> None:
    """Upload the bitstream to the eFPGA.

    :param bitstream_file: The bitstream file to be uploaded.
    :type bitstream_file: str
    :param baudrate: The baudrate to be used for the upload.
    :type bitstream_file: str
    :param ftdi_name: The name of the FTDI chip to be used.
    :type bitstream_file: str
    """

    logger.info(f"Using device at {port}")

    data = read_bitstream_data(bitstream_file)

    logger.info("Uploading bitstream...")

    # Try standard baud rate first
    try:
        with serial.Serial(port, baudrate) as ser:
            ser.write(data)
    except (ValueError, OSError, termios.error if termios else OSError) as e:
        termios_errno = e.args[0] if getattr(e, "args", None) else None
        should_fallback = (
            isinstance(e, ValueError)
            or getattr(e, "errno", None) == errno.EINVAL
            or termios_errno == errno.EINVAL
        )
        if not should_fallback:
            raise

        # If standard baud rate fails, try custom baud rate
        logger.info(f"Standard baud rate failed, attempting custom baud rate {baudrate}...")
        with serial.Serial(port, 9600) as ser:  # Open with any standard rate first
            set_custom_baudrate(ser, baudrate)
            ser.write(data)

    logger.info("Bitstream transmitted!")


def __parse_arguments() -> argparse.Namespace:
    """Parse the command line arguments.

    :return: The arguments parsed from the command line.
    :rtype: argparse.Namespace
    """

    parser = argparse.ArgumentParser(prog="upload_bitstream.py")
    parser.add_argument(
        "bitstream_file", help="Specifies the bitstream file to be uploaded."
    )
    parser.add_argument(
        "-b",
        "--baudrate",
        help=f"Specifies the baudrate. Defaults to {DEFAULT_BAUDRATE} which is the eFPGAs"
        + " baud rate at 10 MHz in MPW-2.",
        type=int,
        default=DEFAULT_BAUDRATE,
    )
    parser.add_argument(
        "-p",
        "--port",
        help=f"Specifies the port. Defaults to {DEFAULT_PORT} unless --usb-id is used.",
        type=str,
        default=None,
    )
    parser.add_argument(
        "-u",
        "--usb-id",
        help="Specifies USB VID:PID (hex) to select a serial device, e.g. 0403:6001.",
        type=str,
        default=None,
    )

    parser.add_argument(
        "-v",
        "--verbose",
        default=False,
        action="count",
        help="Show detailed log information including function and line number",
    )
    args = parser.parse_args()
    return args


def device_port_exists(port_path) -> bool:
    """
    Check if a given device port exists on Linux.

    Args:
        port_path (str): The device port path (e.g., '/dev/ttyUSB0', 'ttyUSB0', '/dev/ttyACM0')

    Returns:
        bool: True if the device port exists and is a character device, False otherwise
    """

    try:
        # Check if the path exists
        if not os.path.exists(port_path):
            logger.error(
                f"Device port {port_path} does not exist. Please check the conneciton and make sure you selected the correct port."
            )
            return False

        # Get file status
        file_stat = os.stat(port_path)

        # Check if it's a character device (typical for serial ports)
        is_char_device = stat.S_ISCHR(file_stat.st_mode)
        if not is_char_device:
            logger.warning(f"Path {port_path} exists but is not a character device")

        return is_char_device
    except (OSError, IOError) as e:
        # Log the error and exit gracefully
        logger.error(f"Failed to access device port {port_path}: {e}")
        return False


def main() -> None:
    """The main function containing the application logic"""
    args = __parse_arguments()
    setup_logger(args.verbose)

    try:
        port = resolve_target_port(args.port, args.usb_id)
    except ValueError as e:
        logger.error(e)
        sys.exit(1)

    if not device_port_exists(port):
        exit()
    upload_bitstream(args.bitstream_file, args.baudrate, port)


if __name__ == "__main__":
    main()

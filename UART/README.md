# UART

You can use `upload.py` to upload a bitstream over UART to a FABulous FPGA.
It was created using `argparse`, so for a full breakdown of the commands
simply use

```console
./upload.py -h
```

This is the general usage of the command:

```console
upload.py [-h] [-b BAUDRATE] [-p PORT] [-u USB_ID] [-v] bitstream_file
```

### Example Use Case

> [!IMPORTANT]
> Make sure to adjust the files to your local files.

Uploading a bitstream:

```console
./upload.py -p /dev/ttyUSB0 bitstream.bin
```

This uses the default baudrate of 57600 Baud.

Selecting by USB VID:PID:

```console
./upload.py -u 0403:6001 bitstream.bin
```

If both `--port` and `--usb-id` are given, `--usb-id` is tried first. If USB-ID resolution fails, the script logs a warning and falls back to `--port`.

"""
GreyEnergy unpacker
===================

Tested on the following samples:
* b60c0c04badc8c5defab653c581d57505b3455817b57ee70af74311fa0b65e22
* d4e97a18be820a1a3af639c9bca21c5f85a3f49a37275b37fd012faeffcb7c4a

Author   : Alessandro Di Pinto (@adipinto)
Reviewer : Guglielmo Fachini
Contact  : secresearch [ @ ] nozominetworks [ . ] com
"""

import os
import pefile
import struct
import hashlib
import logging
import argparse

class GreyEnergyUnpacker:
    def __init__(self, key_len=0x28):
        self.key_len = key_len

    def __init_keymap(self, key):
        ikey = 0
        keysum = 0
        keymap = bytearray([i for i in range(256)])
        for idx in range(len(keymap)):
            keysum = (keysum + key[ikey] + keymap[idx]) % 256
            keymap[idx], keymap[keysum] = keymap[keysum], keymap[idx]
            ikey = (ikey + 1) % len(key)
        return keymap

    def __decrypt(self, cipher, keymap):
        ikey = 1
        keysum = 0
        for idx in range(len(cipher)):
            keysum = (keysum + keymap[ikey]) % 256
            keymap[ikey], keymap[keysum] = keymap[keysum], keymap[ikey]
            keymap_idx = (keymap[ikey] + keymap[keysum]) % 256
            cipher[idx] ^= keymap[keymap_idx]
            ikey = (ikey + 1) % 256
        return cipher

    def __recreate_string_from_prefixes(self, dictionary, index):
        reversed_string = bytearray()
        while True:
            last_byte, index = dictionary[index]
            reversed_string.append(last_byte)
            if index == None:
                break
        reversed_string.reverse()
        return reversed_string

    def __unpack_indexes(self, data):
        idx = 0
        data_left = len(data)
        while idx < len(data):
            if data_left < 3:
                break

            b1, b2, b3 = struct.unpack("BBB", data[idx:idx+3])
            for index in [((b2 & 0xf0) << 4) | b1, ((b2 << 8) | b3) & 0xfff]:
                if index == 0xfff:
                    # END OF INPUT
                    return
                else:
                    yield index

            data_left -= 3
            idx += 3

    def __decompress(self, data):
        unpacked_indexes = self.__unpack_indexes(data)
        decompressed_bytes = bytearray()

        # The algorithm is very similar to LZW, described at the link:
        # http://warp.povusers.org/EfficientLZW/part5.html

        dictionary = [(i, None) for i in range(256)]
        index = next(unpacked_indexes)
        decompressed_bytes.append(index)
        last_index = index

        for index in unpacked_indexes:
            # is the index in the dictionary?
            if index < len(dictionary):
                current_string = self.__recreate_string_from_prefixes(dictionary, index)
                decompressed_bytes += current_string
                b = current_string[0]
                dictionary.append((b, last_index))
            else:
                last_string = self.__recreate_string_from_prefixes(dictionary, last_index)
                b = last_string[0]
                dictionary.append((b, last_index))
                last_string.append(b)
                decompressed_bytes += last_string

            last_index = index

            if len(dictionary) == 4095:
                # max dictionary size reached, reset it
                dictionary = [(i, None) for i in range(256)]

        return decompressed_bytes

    def __extract_pe_overlay(self, data):
        try:
            pe = pefile.PE(data=data, fast_load=True)
            return bytearray(pe.get_overlay())
        except:
            return None

    def unpack(self, data):
        pe_overlay = self.__extract_pe_overlay(data)
        if pe_overlay is None:
            return None

        encrypted_data, key = pe_overlay[self.key_len:], pe_overlay[0:self.key_len]
        keymap = self.__init_keymap(key)
        decrypted_data = self.__decrypt(encrypted_data, keymap)

        decompressed_data = self.__decompress(decrypted_data)

        try:
            pe = pefile.PE(data=decompressed_data, fast_load=True)
            backdoor = pe.get_overlay()
            backdoor_offset = pe.get_overlay_data_start_offset()
            dropper = decompressed_data[0:backdoor_offset]
            return dropper, backdoor
        except:
            return None

def sha256sum(fname):
    h = hashlib.sha256()
    with open(fname, 'rb') as f:
        for bblock in iter(lambda: f.read(4096),b""):
            h.update(bblock)
        return h.hexdigest()

def scandir(path_list):
    print(path_list)
    if not path_list:
        return
    for path in path_list:
        if os.path.isfile(path):
            yield path
        else:
            for root, _, files in os.walk(path):
                for f in files:
                    fpath = os.path.join(root, f)
                    if not os.path.isfile(fpath):
                        continue
                    yield os.path.basename(root), fpath

if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("-f", metavar="DIR/FILE", dest="scan", help="File(s) to unpack", nargs='*', required=True)
    parser.add_argument("-d", dest="dropper", help="Dump the dropper component", action='store_true', default=False)
    parser.add_argument("-o", metavar="OUTPUT", dest="outdir", help="Output directory for the unpacked files")
    args = parser.parse_args()

    # Init logging system
    logging.basicConfig(format="[%(asctime)s] %(levelname)s : %(message)s", level=logging.INFO)

    unpacker = GreyEnergyUnpacker()
    for fpath in scandir(args.scan):
        fhash = sha256sum(fpath)
        fbase = os.path.basename(fpath)
        logging.info("Processing the file '%s' (SHA256 %s)", fpath, fhash)

        with(open(fpath, 'rb')) as f:
            data = bytearray(f.read())

        unpacker_result = unpacker.unpack(data)
        if unpacker_result is None:
            logging.info("The sample does not seem to be packed")
            continue
        dropper, backdoor = unpacker_result

        outpath = os.path.dirname(fpath) if not args.outdir else args.outdir
        if args.dropper is True:
            fdropper = os.path.join(outpath, "%s_dropper_unpacked.bin" % fbase)
            with open(fdropper, 'wb') as f:
                f.write(dropper)
            logging.info("Dropper unpacked in '%s' (SHA256 %s)", fdropper, sha256sum(fdropper))

        funpack = os.path.join(outpath, "%s_malware_unpacked.bin" % fbase)
        with open(funpack, 'wb') as f:
            f.write(backdoor)
        logging.info("Malware unpacked in '%s' (SHA256 %s)", funpack, sha256sum(funpack))

    exit(0)

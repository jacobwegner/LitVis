from pathlib import Path

from lxml import etree
import numpy as np
import pandas as pd

import location


ignore_tokens = [
    "",
    "Scr.",
    "Scr.,",
]

names = [
    "Attici Ficuleano",
    "Pompeiano"
]

no_modify_locations = [
    "Sidae",
    "Menturnis",
    "Iconium"
]

names_tokenized = [name.split(' ') for name in names]


class EpiDocXMLParser():
    def __init__(self, epidoc_fpath, geodetic_fpath, topo_fpath):
        self.epidoc_fpath = Path(epidoc_fpath)
        self.geodetic_fpath = Path(geodetic_fpath)
        self.topo_fpath = Path(topo_fpath)
        # get edition div, which contains all relevant info
        self.tree = etree.parse(str(self.epidoc_fpath))
        edition_path = "./{*}text/{*}body/{*}div[@type='edition']"
        edition_divs = self.tree.findall(edition_path)
        if len(edition_divs) > 1:
            raise ValueError("Expected XML file with one edition div")
        self.edition_div = edition_divs[0]
        # define selectors used for different tags
        # TODO resolve issue with inconsistent capitalization for subtype
        self.sels = {
            "books": "{*}div[@type='textpart'][@subtype='Book']",
            "letters": "{*}div[@type='textpart'][@subtype='letter']",
            "opener": "{*}label[@rend='opener']",
            "dateline": "{*}seg[@rend='dateline']",
            "date": "{*}date",
        }
        # get tags used by methods
        self.datelines = self.edition_div.findall(
            self._rel_path([
                self.sels["books"],
                self.sels["letters"],
                self.sels["opener"],
                self.sels["dateline"],
            ])
        )
        self.letters = self.edition_div.findall(
            self._rel_path([
                self.sels["books"],
                self.sels["letters"],
            ])
        )
        self.letters_text = ["".join(letter.xpath("descendant-or-self::text()"))
                             for letter in self.letters]
        # store parsing outputs
        self.dateline_id = self._parse_dateline_id()
        self.dateline_text = self._parse_dateline_text()
        self.dateline_locs = self._parse_dateline_locs()
        self.dateline_dates = self._parse_dateline_dates()
        # store unique locations as a sorted array
        self.sorted_unique_locs, self.unique_loc_counts = self.unique(
            self.dateline_locs,
            return_counts=True
        )
        self.loc_to_count = dict(zip(self.sorted_unique_locs, self.unique_loc_counts))
        # store unique dates as a sorted array
        self.unique_dates = self.unique(self.dateline_dates)
        # get dictionaries mapping known locations to lat/long and topojson id
        self.loc_to_geodetic = location.loc_to_geodetic(self.geodetic_fpath)
        self.loc_to_topo = location.loc_to_topo(self.topo_fpath)
        # create a DataFrame collating parsed data
        self.dataframe = self._create_dataframe()

    def save_dataframe(self):
        path_stub = Path("data/output", self.epidoc_fpath.stem)
        path_stub.mkdir(exist_ok=True)
        self.dataframe.to_csv(path_stub / "dataframe.csv", index=False)

    def save_csvs(self):
        """Save CSVs with data parsed from document
        """
        path_stub = Path("data/output", self.epidoc_fpath.stem)
        path_stub.mkdir(exist_ok=True)
        np.savetxt(path_stub / "dateline.csv", self.dateline_text[:, np.newaxis], fmt='"%s"',
                   delimiter=',')
        np.savetxt(path_stub / "locations.csv",
                   np.vstack((self.dateline_locs, self.dateline_text)).T,
                   fmt='"%s"', delimiter=',')
        np.savetxt(path_stub / "sorted_locs.csv", self.sorted_unique_locs[:, np.newaxis],
                   fmt='"%s"', delimiter=',')

    def unique(self, array, return_counts=False):
        return np.unique(array[np.not_equal(array, None)], return_counts=return_counts)

    def _create_dataframe(self):
        dateline_geodetic = np.array(
            [self.loc_to_geodetic.get(loc, ["", ""]) for loc in self.dateline_locs])
        dateline_loc_count = np.array(
            [self.loc_to_count.get(loc, 0) for loc in self.dateline_locs])
        dateline_topo = np.array(
            [self.loc_to_topo.get(loc, "") for loc in self.dateline_locs])
        # aggregate counts for topojson ids
        id_to_count = {}
        for loc, id in self.loc_to_topo.items():
            id = str(id)
            id_to_count[id] = id_to_count.get(id, 0) + self.loc_to_count.get(loc, 0)
        dateline_topo_count = np.array(
            [id_to_count.get(id, 0) for id in dateline_topo])
        # define dataframe columns
        col_to_data = {
            "edition": self.dateline_id[:, 0],
            "book": self.dateline_id[:, 1],
            "letter": self.dateline_id[:, 2],
            "date": self.dateline_dates,
            "location": self.dateline_locs,
            "location_lat": dateline_geodetic[:, 0],
            "location_long": dateline_geodetic[:, 1],
            "location_count": dateline_loc_count,
            "topo_id": dateline_topo,
            "topo_count": dateline_topo_count,
        }
        return pd.DataFrame(col_to_data)

    def _parse_dateline_text(self):
        return np.asarray(
            [etree.tostring(dateline, encoding=str, method="text").strip()
             for dateline in self.datelines]
        )

    def _parse_dateline_id(self):
        # store library.edition.language, book, and letter id
        ids = []
        for dateline in self.datelines:
            letter = dateline.getparent().getparent()
            book = letter.getparent()
            ids.append([self.epidoc_fpath.stem, book.attrib["n"], letter.attrib["n"]])
        return np.array(ids, dtype=str)

    def _parse_dateline_locs(self):
        datelines = self.dateline_text
        locations = []
        # get one location for every dateline
        for dateline in datelines:
            location = ""
            # remove bad characters for easier parsing
            bad_chars = [",", ";"]
            for bad_char in bad_chars:
                dateline = dateline.replace(bad_char, "")
            # tokenize dateline for easier parsing logic
            tokens = dateline.split(" ")
            i = 0
            while i < len(tokens):
                token = tokens[i]
                if i < len(tokens) - 1:
                    next_token = tokens[i + 1]
                # skip useless tokens
                if token in ignore_tokens:
                    pass
                # names not to modify
                elif token in no_modify_locations:
                    location = token
                elif next_token in no_modify_locations:
                    location = next_token
                # parse words with uppercase first letter as location
                elif token[0].isupper():
                    if token == "Patris":
                        # unsure about mapping
                        location = "Patra"
                    elif token[-2:] == "ae":
                        location = token[:-1]
                    elif token[-2:] == "is":
                        location = token[:-2] + "s"
                    elif token[-2:] == "ii":
                        location = token[:-1] + "um"
                    elif token[-1:] == "i":
                        location = token + "um"
                    else:
                        # raise ValueError("Unexpected dateline token for location: " + token)
                        pass
                # parse words after "in"
                elif token == "in":
                    # parse people's houses by checking for matching name
                    for j, name_tokens in enumerate(names_tokenized):
                        for k, name_token in enumerate(name_tokens):
                            if name_token != tokens[i + k + 1]:
                                break
                            elif k == len(name_tokens) - 1:
                                location = names[j] + " house"
                        if location is not None:
                            break
                    if location is None:
                        if next_token[0].isupper():
                            if next_token[-1] == "o":
                                location = next_token[:-1] + "um"
                            else:
                                location = next_token
                # parse words after "ad"
                elif token == "ad":
                    if next_token[0].isupper():
                        if next_token[-2:] == "um":
                            location = next_token[:-1] + "s"
                # parse "ibidem"
                elif token == "ibidem" or token == "ibid":
                    location = locations[-1]
                else:
                    # raise ValueError("Unexpected dateline token: " + token)
                    pass

                # stop if location is found
                if location != "":
                    break
                i += 1
            locations.append(location)
        return np.asarray(locations)

    def _parse_dateline_dates(self):
        dates = []
        # get one date for every dateline
        for dateline in self.datelines:
            date = None
            # get the date from a date tag within the dateline
            date_tag = dateline.find(self.sels["date"])
            if date_tag is not None:
                date = date_tag.attrib.get("when")
            # get date from parenthesis at end of dateline
            if date is None:
                dateline_text = etree.tostring(dateline, encoding=str, method="text").strip()
                # find string of numbers before a close parenthesis
                end_ind = dateline_text.rfind(")")
                start_ind = end_ind - 1
                while start_ind > 0:
                    if dateline_text[start_ind].isnumeric():
                        start_ind -= 1
                    else:
                        break
                start_ind += 1
                if start_ind < end_ind:
                    date = "-" + dateline_text[start_ind:end_ind]
            # convert str to int
            if date is not None:
                # convert to AUC
                date = str(int(date) + 754)
            else:
                date = ""
            dates.append(date)
        return np.asarray(dates)

    def _rel_path(self, selectors):
        """Generates relative path from tag selectors

        Args:
            selectors (iterable): Iterable of selectors to apply
        """
        return "./" + "/".join(selectors)

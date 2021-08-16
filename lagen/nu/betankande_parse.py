import bs4
import json
import sys
import re
import os.path
import urllib.parse

ACTION_RE = re.compile(r"""
  (?P<status>bifaller|avslår) \s*
  (riksdagen)? \s*
  (?P<partial>delvis|i \s+ denna \s+ del)? \s*
  (?P<type>proposition|motion)(en)? \s*
  (?P<id>[0-9]{4}/[0-9]{2}:[A-Za-z0-9]+) \s*
  ((punkterna|yrkandena|punkt|yrkande|punkten|yrakandet) \s*
   (?P<points>
    [-0-9]+ \s*
    (delvis|i \s+ denna \s+ del)? \s*
    ((och|samt|,) \s*
     [-0-9]+ \s*
     (delvis|i \s+ denna \s+ del)? \s*
    )*
   )
  )?""", re.VERBOSE)
ACTION_DETAIL_RE = re.compile(r"""
  ^
  .*
  antar \s*
  (?P<parts> .*) \s
  (?P<actor> [^\s]+) \s
  förslag \s till \s
  (?P<law> lag \s om [^.]*?)
  (med \s den .*)?
  [.]?
  $
""", re.VERBOSE | re.MULTILINE)
PARA_RE = re.compile(r"""
(?P<chapter> [0-9]+) \s kap. \s
(?P<points> [-0-9]+ \s* ([a-z] \s)? \s* ((och|samt|,) \s+ [-0-9]+ \s* ([a-z] \s)? \s* )*) \s* §+
""", re.VERBOSE | re.MULTILINE)
MULTIACTION_RE = re.compile(r"(?P<status>bifaller|avslår)\s*(riksdagen)?\s*(?P<partial>delvis|i denna del)?\s*(?P<type>propositionerna|motionerna)")
MULTIACTION_ITEM_RE = re.compile(r"""
  ^
  (?P<id>[0-9]{4}/[0-9]{2}:[A-Za-z0-9]+) \s+  
  (av \s+ (?P<author>[^0-9]*?))?
  ((punkterna|yrkandena|punkt|yrkande|punkten|yrakandet) \s*
   (?P<points>
    [-0-9]+ \s*
    ((och|samt|,) \s*
     [-0-9]+ \s*)*
   )
  )?
  \s*
  (och|samt|,) \s*
  \s*
  $""", re.VERBOSE | re.MULTILINE)
MULTIACTION_DETAIL_RE = re.compile(r"""
  .*
  antar \s*
  (?P<actor> [^\s]+) \s
  förslag \s till \s*[\n]+
  (?P<laws>([0-9]+[.].*[\n]+)*)
""", re.VERBOSE | re.MULTILINE)
MULTIACTION_DETAIL_ITEM_RE = re.compile(r"""
  ^
  [0-9]+[.] \s+
  (?P<parts> .*) \s*
  (?P<law> lag \s om .*?)
  ((med \s den|i \s de \s delar) .*)?
  $
""", re.VERBOSE | re.MULTILINE)
POINT_SPLIT_RE = re.compile(r"\s*(?:samt|och|,)\s*")

class ParseBetankande(object):
    def genlawurl(self, **graph):
        attributeorder = ["K", "P", "S", "N"]
        if "L" in graph:
            urifragment = graph["L"]        
        else:
            urifragment = self.laws.get(graph["T"].strip("\n\t ,.").lower(), "unknown/%s" % urllib.parse.quote(graph["T"]))
        for key in attributeorder:
            if key in graph:
                if "#" not in urifragment:
                    urifragment += "#"
                urifragment += key + graph[key].replace(" ", "")
        return "http://rinfo.lagrummet.se/publ/sfs/%s" % urifragment    

    def add_item(self, name, value, l=None):
        if l is None:
            l = self.pthlen + 1
        c = self.contents
        for pthitem in self.pth[:l]:
            if pthitem not in c:
                c[pthitem] = {}
            c = c[pthitem]
        c[name] = value


    def add_action_item(self, status, id_type, id, point = ""):
        types = {"proposition": "prop",
                 "motion": "mot"}
        id_type = types.get(id_type, id_type)
        if point:
            point = "#P%s" % (point,)
        self.add_item("http://rinfo.lagrummet.se/publ/%s/%s%s" % (id_type, id, point), status, 2)
        
    def add_action(self, groups):
        if groups["points"]:
            for point in re.split(POINT_SPLIT_RE, groups["points"]):
                partial = False
                if groups["partial"]:
                    partial = "partial"
                if point.endswith("i denna del"):
                    partial = "partial"
                    point = point[:-len("i denna del")]
                if point.endswith("delvis"):
                    partial = "partial"
                    point = point[:-len("delvis")]
                if "-" in point:
                    start, end = point.split("-")
                    start = int(start)
                    end = int(end)
                    for point in range(start, end+1):
                        self.add_action_item(partial or groups["status"], groups["type"], groups["id"], point)
                else:
                    point = int(point)
                    self.add_action_item(partial or groups["status"], groups["type"], groups["id"], point)
        else:
            partial = False
            if groups["partial"]:
                partial = "partial"
            self.add_action_item(partial or groups["status"], groups["type"], groups["id"])

    def handle_content(self):
        s = "\n".join(self.content)
        del self.content[:]

        if "Kammaren biföll utskottets förslag" in s:
            self.add_item("resultat", True, 2)
        if "Beslut fattat med acklamation." in s:
            self.add_item("votering", "acklamation", 2)
        for m in re.finditer(ACTION_RE, s):
            group = m.groupdict()
            group["status"] = group["status"] == "bifaller"
            self.add_action(group)
        for m in re.finditer(ACTION_DETAIL_RE, s):
            law = m.groupdict()
            if law["parts"].strip():
                for pm in re.finditer(PARA_RE, law.pop("parts")):
                    chapter = pm.groupdict()
                    for point in re.split(POINT_SPLIT_RE, chapter["points"]):
                        self.add_item(self.genlawurl(T=law["law"], K=chapter["chapter"], P=point), True, 2)
            else:
                self.add_item(self.genlawurl(T=law["law"]), True, 2)
        for m in re.finditer(MULTIACTION_DETAIL_RE, s):
            for mlaw in re.finditer(MULTIACTION_DETAIL_ITEM_RE, m.groupdict()["laws"]):
                law = mlaw.groupdict()
                if law["parts"].strip():
                    for pm in re.finditer(PARA_RE, law.pop("parts")):
                        chapter = pm.groupdict()
                        for point in re.split(POINT_SPLIT_RE, chapter["points"]):
                            self.add_item(self.genlawurl(T=law["law"], K=chapter["chapter"], P=point), True, 2)
                else:
                    self.add_item(self.genlawurl(T=law["law"]), True, 2)

        multiactionsep = [(m.end(), m.groupdict()) for m in re.finditer(MULTIACTION_RE, s)] + [(len(s) + 1, None)]
        for (start, startgrp), (end, endgrp) in zip(multiactionsep[:-1], multiactionsep[1:]):
            for m in re.finditer(MULTIACTION_ITEM_RE, s[start:end]):
                groups = m.groupdict()
                groups["type"] = startgrp["type"].replace("erna", "")
                groups["status"] = startgrp["status"] == "bifaller"
                groups["partial"] = startgrp["partial"]
                self.add_action(groups)

    def handle(self, item):
        if item.name and item.name.startswith("h"):
            self.handle_content()
            clss = set(item.attrs["class"]).intersection(self.levels.keys())
            if clss:
                self.pthlen = self.levels[next(iter(clss))]
                self.pth[self.pthlen] = item.string.strip()
        elif item.name and item.name == "strong":
            self.handle_content()
            self.pthlen += 1
            self.pth[self.pthlen] = item.string.strip()
        elif hasattr(item, "find_all") and item.find_all(class_="button-more"):
            self.add_item("votering", "https://www.riksdagen.se" + item.find_all(class_="button-more")[0].attrs["data-overlay-url"], 2)
        elif item.name and item.name == "p":
            for sub in item.children:
                self.handle(sub)
        elif hasattr(item, "find_all") and item.find_all("a"):
            self.add_item("links", {lnk.get_text().strip(): "https://www.riksdagen.se" + lnk.attrs["href"]
                               for lnk in item.find_all("a")})
        else:
            if hasattr(item, "get_text"):
                self.content.append(item.get_text())
            else:
                self.content.append(item.string)


    def get_laws(self):
        with open(self.path + "/lawlist") as f:
            s = f.read()
        s = bs4.BeautifulSoup(s, features="lxml")
        laws = {}
        for tr in s.find("table").find_all("tr"):
            idtd = tr.find(attrs={"data-lable": "SFS-nummer"})
            if not idtd: continue
            id = idtd.get_text().strip()
            title = tr.find(attrs={"data-lable": "Rubrik"}).get_text().strip("\n\t ,.").lower()
            laws[title] = id
        return laws

    def __new__(cls, path):
        self = object.__new__(cls)

        self.path = path
        with open(path + "/index.html") as f:
            content = f.read()
        
        self.pth = [None, None, None, None]
        self.pthlen = 0
        self.levels = {"big": 0, "medium": 1, "small": 2}
        self.contents = {}
        self.content = []
        
        self.bet_document = bs4.BeautifulSoup(content, features="lxml")

        self.laws = self.get_laws()

        header = self.bet_document.find(class_="module-header")
        self.contents["title"] = header.find(class_="biggest").get_text().strip()
        self.contents["id"] = header.find(class_="big").get_text().split("betänkande")[1].strip()
        
        for col in self.bet_document.find(id="step4").find_all(class_="columns"):
            for item in col.children:
                self.handle(item)

        for key, value in self.contents["Förslagspunkter och beslut i kammaren"].items():
            if "resultat" not in value:
                value["resultat"] = False
                
        return self.contents

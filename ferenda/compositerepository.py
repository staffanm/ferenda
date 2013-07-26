from . import DocumentRepository


class CompositeRepository(DocumentRepository):
    instances = {}

    @classmethod
    def get_instance(cls, instanceclass, options={}):
        if not instanceclass in cls.instances:
            # print "Creating a %s class with options %r" % (instanceclass.__name__,options)
            cls.instances[instanceclass] = instanceclass(options)
        return cls.instances[instanceclass]

    @classmethod
    def list_basefiles_for(cls, action, basedir):
        if action == "parse_all":
            documents = set()
            for c in cls.subrepos:
                # instance = cls.get_instance(c)
                for basefile in c.list_basefiles_for("parse_all", basedir):
                    if basefile not in documents:
                        documents.add(basefile)
                        yield basefile
        elif action in ("generate_all", "relate_all"):
            #super(CompositeRepository,cls).list_basefiles_for(action,basedir)
            # copied code from DocumentRepository.list_basefiles_for --
            # couldn't figure out how to call super on a generator
            # function.
            directory = os.path.sep.join((basedir, cls.alias, "parsed"))
            suffix = ".xhtml"
            for x in util.list_dirs(directory, suffix, reverse=True):
                yield cls.basefile_from_path(x)

    def __init__(self, options):
        self.myoptions = options
        super(CompositeRepository, self).__init__(options)
        if 'log' in self.myoptions:
            # print "Log set: %s" % self.myoptions['log']
            pass
        else:
            # print "Setting log to %s" % self.log.getEffectiveLevel()
            self.myoptions['log'] = logging.getLevelName(
                self.log.getEffectiveLevel())
        for c in self.subrepos:
            inst = self.get_instance(c, dict(self.myoptions))
            # print "DEBUG: Inst %s: log level %s" % (inst, logging.getLevelName(inst.log.getEffectiveLevel()))

    def download(self):
        for c in self.subrepos:
            inst = c(options=self.myoptions)
            inst.download()

    def parse(self, basefile):
        start = time()
        self.log.debug("%s: Starting", basefile)
        for c in self.subrepos:
            inst = self.get_instance(c, self.myoptions)
            inst.log.setLevel(logging.INFO)
            if os.path.exists(inst.downloaded_path(basefile)):
                if os.path.exists(inst.parsed_path(basefile)):
                    self.log.debug("%s: Using previously-created result (by %s)" %
                                   (basefile, inst.__class__.__name__))
                    self.copy_parsed(basefile, inst)
                    return True
                elif inst.parse(basefile):
                    self.log.info("%s: Created %s (using %s)" %
                                  (basefile, self.parsed_path(basefile), inst.__class__.__name__))
                    self.copy_parsed(basefile, inst)
                    self.log.info(
                        '%s: OK (%.3f sec)', basefile, time() - start)
                    return True
        return False
        # instancelist = ", ".join([x.__class__.__name__ for x in instances])
        # self.log.debug("%s in %d repos (%s)" %
        #               (basefile, len(instances),instancelist))
        # self.join_parsed(basefile,instances)

    def copy_parsed(self, basefile, instance):
        # If the distilled and parsed links are recent, assume that
        # all external resources are OK as well
        if (util.outfile_is_newer([instance.distilled_path(basefile)],
                                  self.distilled_path(basefile)) and
            util.outfile_is_newer([instance.parsed_path(basefile)],
                                  self.parsed_path(basefile))):
            self.log.debug(
                "%s: External resources are (probably) up-to-date" % basefile)
            return

        cnt = 0
        for src in instance.list_external_resources(basefile):
            cnt += 1
            target = (os.path.dirname(self.parsed_path(basefile)) +
                      os.path.sep +
                      os.path.basename(src))
            util.link_or_copy(src, target)

        util.link_or_copy(instance.distilled_path(basefile),
                          self.distilled_path(basefile))

        util.link_or_copy(instance.parsed_path(basefile),
                          self.parsed_path(basefile))

        if cnt:
            self.log.debug("%s: Linked %s external resources from %s to %s" %
                           (basefile,
                            cnt,
                            os.path.dirname(instance.parsed_path(basefile)),
                            os.path.dirname(self.parsed_path(basefile))))

    # Not used -- see copy_parsed instead
    def join_parsed(self, basefile, instances):
        # The default algorithm for creating a joined/composite result:
        # 1. Load all distilled files and add any unique triple to a
        #    composite graph
        composite = Graph()
        # FIXME: Construct this list of bound namespaces dynamically somehow)
        composite.bind('dct', self.ns['dct'])
        composite.bind('rpubl', self.ns['rpubl'])
        composite.bind('xsd', self.ns['xsd'])
        composite.bind('foaf', self.ns['foaf'])
        composite.bind('xhv', self.ns['xhv'])

        for inst in instances:
            if os.path.exists(inst.distilled_path(basefile)):
                g = Graph()
                g.parse(inst.distilled_path(basefile))
                composite += g

        distilled_file = self.distilled_path(basefile)
        util.ensure_dir(distilled_file)
        composite.serialize(
            distilled_file, format="pretty-xml", encoding="utf-8")

        # 2. Use the first produced xhtml file (by the order specified
        # in self.supbrepos)
        #
        # FIXME: The trouble with this is that our distilled RDF/XML
        # file will most often contain a superset of all RDF triples
        # found in one particular XHTML+RDFa file.
        for inst in instances:
            if os.path.exists(inst.parsed_path(basefile)):
                self.copy_external_resources(basefile, inst)

    # Not sure this belongs in CompositeRepository -- maybe should be
    # part of the base implementation, or maybe we shouldn't copy
    # resources like this at all (instead make sure the server serves
    # resources up from the parsed directory)?
    def generate(self, basefile):
        # create self.generated_path(basefile)
        super(CompositeRepository, self).generate(basefile)

        # then link all other files from parsed that are not self.parse_path(basefile)
        # FIXME: dup code of copy_parsed and Regeringen.list_resources()
        parsed = self.parsed_path(basefile)
        resource_dir = os.path.dirname(parsed)
        for src in [os.path.join(resource_dir, x) for x in os.listdir(resource_dir)
                    if os.path.join(resource_dir, x) != parsed]:
            target = (os.path.dirname(self.generated_path(basefile)) +
                      os.path.sep +
                      os.path.basename(src))
            self.log.debug("Linking %s to %s" % (target, src))
            util.link_or_copy(src, target)

Decouple-generate
=================

This is a feature branch that aims to decouple the parts of ferenda
that generate browser-ready HTML5 from the rest of the framework.

Stuff to move or remove
-----------------------

This includes the following methods from DocumentRepository:

generate
generate_all_setup
generate_all_teardown
get_url_transform_func
prep_annotation_file
graph_to_annotation_file
annotation_file_to_graph
toc
toc_select
toc_query
toc_criteria
toc_predicates
toc_pagesets
toc_select_for_pages
toc_item
toc_generate_pages
toc_generate_first_page
toc_generate_page
frontpage_content

(Note that construct_annotations should still be a part of
DocumentRepository, but be called by a new action called "annotate" or
something like that)

And the following functions from manager.py:

makeresources
_process_file
frontpage
_wsgi_search (part of, maybe)

The main idea
-------------

The main idea is that core ferenda should provide functionality for
downloading, parsing and relating data. Core ferenda should still use
a fulltextindex and a triplestore, and be able to serve basic data
through WSGI.

data/
    base/
        downloaded/
	parsed/
	distilled/
	annotations/ -- in .ttl or .rdf/xml, not .grit, format
	news/ -- might be renamed to feed and not include HTML5 files

At the same time (but in another branch), we're constructing a more
full-featured API. This API should be rich enough that we can build a
single page javascript app (SPA) that provides a usable website. This
API will require only the parsed & distilled files + triplestore and
fulltext index.

Generators
----------

The parts that generate a full browser-ready website consisting of
static HTML5 files should live in a separate part (which probably
should be included in the main distribution).

As a proof of concept, the work in this branch will attempt to
implement a jinja2-based transformation engine, a couple of templates
and then drive the entire transformation (generate, toc, news/html,
frontpage) using that and some new jinja2 templates.

Once that's done, we remove all the above functions from
ferenda.DocumentRepository


Other "generators"
------------------

A flask (or maybe django) app which takes the datadir from a core
ferenda setup, and creates a website for it. It could probably re-use
the above jinjja2 templates, but at least its TOCs will be driven by
live SQLite queries.

Questions:

Q: Who selects which transformation engine to use, the user or the
   docrepo author?

A: The docrepo provides a default which the user may override, but the
   user must make sure that all required templates are available.

Q: How should generate --all, toc, news (the HTML-generating parts)
   and frontpage now be called?

A1: '$ ./ferenda-build w3c generate 123/a'
   - manager.py instantiates a W3Standards object in the usual way (docrepo)
   - manager.py looks up docrepo.config.w3c.transformertype (defaults to 'XSLT').
   - depending on transformertype, manager.py instantiates a ferenda.generator.Jinja2 object (gen)
   - manager.py calls gen.generate(templatefile, config, annotations, parsedfile, outfile)
     ie gen.generate("res/jinja/generic.jinja",
                     docrepo.config (?), # when do makeresources get a chance to move and minify resources?
		     Graph().parse(docrepo.annotation_path("123/a")),
		     docrepo.parsed_path("123/a"),
		     docrepo.generated_path("123/a"))
     or gen.generate("res/xsl/generic.xsl",
                     "data/rsrc/resources.xml", # not an adopted file ("resources-depth-4.xml")
		     docrepo.annotation_path("123/a"), # must transform rdf/xml to grit first
		     docrepo.parsed_path("123/a"),
		     docrepo.generated_path("123/a"))

     or maybe:
     cls = getclass(docrepo.config.w3c.transformertype)
     gen = cls(docrepo) # and config?
     gen.generate("123/a")

A2: ./ferenda-build.py w3c toc --  manager.py know to instantiate
    ferenda.generator.TOC (or any other subclass!)

Q: How should they be called from the python API?

A2:  myrepo = MyRepo(datadir="foo", otherparam="bar")
     import ferenda.generator.xslt as cls
     gen = cls(docrepo, **config)
     gen.toc() # calls into docrepo.select_for_pages (or somesuch)

Q: How is toc pages customized?

A: toc(), unlike generate(), performs both data processing (selecting
   the dataset and so on) and transformation into a target doc(). The
   data processing step should be kept separate (maybe still in in the
   docrepo after all?). If theyre kept in the docrepo, we could expose
   them as part of the API!

Q: What happens to ferenda.Transformer ?
A: Maybe it lives on as a low-level tool? Maybe the generate code can
   be sufficiently abstracted that there is no need for
   ferenda.generate.xslt and ferenda.generate.jinja, just a basic
   sub-package that is initialized with either 'XSLT' or 'JINJA'?


Start of impl
-------------

# ferenda.htmlgenerator.jinja2


def my_generate(basefile, instance, **kwargs):
    # objectify does the inverse of render_xhtml (as far as possible)
    docbody = objectify(instance.store.parsed_path(basefile))
    graph = Graph().parse(instance.store.annotation_path(basefile))

    # decoupling: the template to use must not be hardcoded into docrepo instance. 
    # instead it should be read from config (which maybe won't have a docrepo-derived
    # default)
    template = inst.config.generatetemplate 
    templatedirs = inst.config.templatedirs 
    documentroot = inst.config.datadir
    # not sure what the output and side effects of make_jinja_config is yet
    jinjaconfig = make_jinja_config(instance.config)
    t = Transformer('JINJA2', template, templatedirs, documentroot, jinjaconfig)
    depth = compute_depth(documentroot, outfile)
    with instance.store.open_generated(basefile, "wb") as fp:
       fp.write(t.transform(docbody, depth, inst.get_url_transform()).write)

   
def my_toc(instance, *args, **kwargs):
    pass

def my_news(instance):
    pass

def my_frontpage(instance):
    pass
   

# specify:
# - action name
# - callable
# - how to handle --all


manager.register_action("jmakeresources", my_makeresources, None)

manager.register_basefile_action("jgenerate", my_generate, "generate")

# ferenda.manager 
def register_action(actionname, callable, list_basefiles_for_arg=None):
    if "actionname" in actions: # actions = global var
        raise DuplicateAction(actionname)
    actions[actionname] = callable
    # do something smart with list_basefiles_for_arg


# ferenda.manager

def _run_class():
    ...
    if command in actions: # registered actions
        clbl = actions[command] 
        # is this possible? to avoid the later if-clauses?
        # clbl = partial(clbl, inst) 
    elif hasattr(inst, command): # Docrepo-derived methods
        clbl = getattr(inst, command)
    else:
        raise UndefinedAction(command)
    
    ...
    if hasattr(inst.config, 'all') and inst.config.all == True:
        cls.setup(command, inst.config)
	# FIXME: need to read command not from argv but from the action registry
        for basefile in inst.store.list_basefiles_for(command):
            if command in actions:
                clbl(basefile, inst, **kwargs)
            else:
                clbl(basefile, **kwargs)
        ...
    else:
        if command in actions:
            res = clbl(inst, *args, **kwargs)
        else:
            res = clbl(*args, **kwargs)

manager.register_action("jgenerate", my_generate, "generate")

manager.register_action("jtoc", my_generate, None)

manager.register_action("jnews", my_generate, None)

manager.register_action("jfrontpage", my_generate, None)





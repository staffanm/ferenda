About this package
==================

The classes in this package extend, combine and configure the documentrepositories under ferenda.sources.legal.se (and some from ferenda.sources.legal.general) so that they 

can build the lagen.nu website. The general idea is that the ferenda.sources.legal.se package should handle information and sources in a way that is 100% compatible with the swedish public legal information system, and that all modifications to create the lagen.nu website should be kept separate.

This is done through three different mechanisms:

1. Different URI structure (and added owl:sameAs data)
------------------------------------------------------
The docrepos in ferenda.sources.legal.se generate URIs for all document using the principles sused by the swedish public legal information system (avilable in swedish at http://dev.lagrummet.se/dokumentation/system/uri-principer.pdf). According to these principles, the URI för the act Rättsinformationsförordningen (1999:175) is http://rinfo.lagrummet.se/publ/sfs/1999:175. 

When publishing the same document on lagen.nu, the URI https://lagen.nu/sfs/1999:175 is used instead. This is so that a linked data consumer can fetch data from that URI and lagen.nu can be in control of what's being returned. 

This is accomplished by having a res/uri/swedishlegalsource.space.ttl file, used by ferenda.thirdparty.coin.URIMinter, within this package that is different from the one used by the ferenda.sources.legal.se package. Every time any part of the code generates an URI, ferenda.Resourceloader makes sure that the correct version of this file is loaded.

To make clear that this URI represents the same thing as the previous URI, an automatic owl:sameAs reference is created (eg <https://lagen.nu/sfs/1999:175> owl:sameAs <http://rinfo.lagrummet.se/publ/sfs/1999:175>. This is done by the mixin class lagen.nu.SameAs, which overrides the infer_metadata method to automatically add this reference.

2. Different resource files
---------------------------
Apart from URI generation, various resource files (particularly res/extra/swedishlegalsource.ttl, containing URIs and information about non-document resources such as organizations and publications) are overridden by the resource files in this package. Again, ferenda.Resourceloader makes sure that the lagen.nu version of a resource is loaded instead of the ferenda.sources.legal.se version, if it exists. Curated, non-official metadata such as popular names and abbreviations for common laws is also present. XSLT stylesheets and SPARQL queries can be overridden and adapted to lagen.nu-specific needs.

3. Additional CompositeRepository and FacadeSource repos
--------------------------------------------------------

ferenda.sources.legal.se defines a couple of CompositeRepository classes wherever there exists more than one source for a given document type.

For lagen.nu, there exists an archive of documents downloaded from the previous version of regeringen.se, possibly containing documents or metadata that are no longer available from regeringen.se. The existing CompositeRepository classes are extended to include legacy classes that read from that archive. A similar sfslegacy class is also implemented, which reads from an archive of the previous version of http://rkrattsbaser.gov.se/

Furthermore, the FacadeSource base class is implemented. This is a form of CompositeRepository that bundle together multiple sub-repositorys, each responsible for a certain document type, for purposes of creating combined TOC pages, newsfeeds and tabs in the navigation UI. Unlike regular CompositeRepository, FacadeSource defers to subrepositorys to handle each document type, but creates a coherent facade for them in the navgation UI. This is used to group similar types (lagen.nu.Forarbeten, lagen.nu.MyndFskr, lagen.nu.MyndPrax) in the UI. This means that instead of having one tab for Direktiv, another for SOU documents, another for propositioner, and so one, they can all be grouped under a "Förarbeten" tab. 



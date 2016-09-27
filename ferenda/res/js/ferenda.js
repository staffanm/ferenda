$(document).ready(function () {
    /* hook up the offcanvas classes to make a sliding left menu possible on small screens */
    $('[data-toggle="offcanvas"]').click(function () {
	$('.row-offcanvas').toggleClass('active')
    });

    /* functions for replacing the text rendering of a pdf page with an image rendering of same */
    $('div.sida a.view-img').click(function () {
	/* hide everything else from here to next page */
	$(this).parents("div.sida").nextUntil("div.sida").hide()

	/* if we haven't reached a pagebreak, we might need to
	 * continue hiding elements in the next top-level div */
	if ($(this).parents("div.sida").siblings("div.sida").length == 0) {
	    nextsectionstart = $(this).parents("div.toplevel").next().find("section *").first();
	    nextsectionstart.hide();
	    nextsectionstart.nextUntil("div.sida").hide();
	    /* FIXME: There might be two top-level sections on a page... */
	}
	navtabs = $(this).parents("ul")

	navtabs.find("li:nth-child(1)").removeClass("active");
	navtabs.find("li:nth-child(2)").addClass("active");
	/* on-demand load facsimileimage and show */
	navtabs.siblings(".facsimile").each(function(idx) {
	    this.src = $(this).attr('data-src');
	    $(this).show();
	})
    });
    $('div.sida a.view-text').click(function() {
	navtabs = $(this).parents("ul")
	navtabs.find("li:nth-child(1)").addClass("active");
	navtabs.find("li:nth-child(2)").removeClass("active");
	navtabs.siblings(".facsimile").hide();
	$(this).parents("div.sida").nextUntil("div.sida").show();
	if ($(this).parents("div.sida").siblings("div.sida").length == 0) {
	    nextsectionstart = $(this).parents("div.toplevel").next().find("section *").first();
	    nextsectionstart.show();
	    nextsectionstart.nextUntil("div.sida").show();
	}
    });
})




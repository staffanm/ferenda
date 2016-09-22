$(document).ready(function () {
    /* hook up the offcanvas classes to make a sliding left menu possible on small screens */
    $('[data-toggle="offcanvas"]').click(function () {
	$('.row-offcanvas').toggleClass('active')
    });

    /* functions for replacing the text rendering of a pdf page with an image rendering of same */
    $('div.sida button.view-facsimile').click(function () {
	/* hide everything else from here to next page */
	$(this).parents("div.sida").nextUntil("div.sida").toggle();
	$(this).parent().find("span").toggle();
	/* on-demand load facsimile image and show */
	$(this).siblings(".facsimile").each(function(idx) {
	    this.src = $(this).attr('data-src');
	    $(this).toggle();
	})
    })
})




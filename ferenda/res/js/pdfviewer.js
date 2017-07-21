$(document).ready(function () {
    $('div.page a.view-img').click(function () {
        /* switch active tabs */
	navtabs = $(this).parents("ul")
	navtabs.find("li:nth-child(1)").removeClass("active");
	navtabs.find("li:nth-child(2)").addClass("active");
	/* hide html rendition of the page */
	$(this).parents("div.page").find("div.pdfpage p").hide()
	/* on-demand load facsimile image and show */
        navtabs.siblings("div.pdfpage").find("img").each(function(idx) {
           this.src = $(this).attr('data-src');
           $(this).show();
	})
    });
  $('div.page a.view-text').click(function() {
        /* switch active tabs */
	navtabs = $(this).parents("ul")
  	navtabs.find("li:nth-child(1)").addClass("active");
        navtabs.find("li:nth-child(2)").removeClass("active");
        /* hide facsimile image and re-show html rendition */
        navtabs.siblings("div.pdfpage").find("img").hide();
        $(this).parents("div.page").find("div.pdfpage p").show();
  });
});

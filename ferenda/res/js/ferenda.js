function toggleOffcanvas() {
  $('.row-offcanvas').toggleClass('active');
}

$(document).ready(function () {
  /* hook up the offcanvas classes to make a sliding left menu
   * possible on small screens -- in three different ways
   1. as a onclickhandler on the slide button (doesn't work, the
      slidebutton doesn't recieve the click)
   2. as a keypress handler, press 'f' to toggle the menu
   3. as a swipeleft handler (doesn't work, at least not on Chrome devtools)
 */
  $('.slidebutton').click(toggleOffcanvas);
  $('body').keydown(function(e) {
    if (e.key == 'f') { toggleOffcanvas() }
  });

  /* this is said to work in situations where doc.body doesn't... */
  body = document.getElementsByTagName('body')[0];
  /* for some bizarre reason, hammmer.js disables text selection by
   * default...  That's strike two against this lib (first being
   * indecipherable API), see
   * https://github.com/hammerjs/hammer.js/issues/81 */
  
  /*
  hammer = new Hammer(body, {cssProps: {userSelect: false}});
  hammer.on("swipe", function(e) {
    $('.row-offcanvas').toggleClass('active');
  });
  */
  
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
        navtabs.siblings(".facsimile").children("img").each(function(idx) {
	    this.src = $(this).attr('data-src');
	    $(this).show();
	})
    });
  $('div.sida a.view-text').click(function() {
	navtabs = $(this).parents("ul")
	navtabs.find("li:nth-child(1)").addClass("active");
	navtabs.find("li:nth-child(2)").removeClass("active");
        navtabs.siblings(".facsimile").children("img").hide();
	$(this).parents("div.sida").nextUntil("div.sida").show();
	if ($(this).parents("div.sida").siblings("div.sida").length == 0) {
	    nextsectionstart = $(this).parents("div.toplevel").next().find("section *").first();
	    nextsectionstart.show();
	    nextsectionstart.nextUntil("div.sida").show();
	}
    });

  var suggestions = new Bloodhound({
    datumTokenizer: Bloodhound.tokenizers.obj.whitespace('label'),
    queryTokenizer: Bloodhound.tokenizers.whitespace,
    /*
    prefetch: {
      url: '/rsrc/api/suggestions.json',
      cache: false
    },
    */
    remote: {
      url: '/api/?q=%QUERY&_ac=true',
      wildcard: '%QUERY'
    } 
  });
  promise = suggestions.initialize();
  promise
    .done(function() {
      console.log('ready to go!');
    })
    .fail(function() {
      console.log('err, something went wrong :(');
    });
  $('.navbar-form .typeahead').typeahead(null, {
    name: 'suggestions',
    display: 'label',
    source: suggestions,
    limit: 9,
    templates: {
      suggestion: function(ctx) {
	return "<div class='tt-suggestion'><strong>" + ctx.label + "</strong><br/><small>" + ctx.desc + "</small></div>";
      }
    }
  });
  $('.navbar-form .typeahead').bind('typeahead:select', function(ev, suggestion) {
     window.location.href=suggestion.url
  });
})




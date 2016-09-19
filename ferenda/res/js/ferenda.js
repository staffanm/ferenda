/* own code goes here */

/* hook up the offcanvas classes to make a sliding left menu possible on small screens */
$(document).ready(function () {
  $('[data-toggle="offcanvas"]').click(function () {
    $('.row-offcanvas').toggleClass('active')
  });
});

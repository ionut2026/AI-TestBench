import javax.tools.*;
public class Compile {
  public static void main(String[] a) {
    JavaCompiler c = ToolProvider.getSystemJavaCompiler();
    int rc = c.run(null, null, null, a);
    System.exit(rc);
  }
}
